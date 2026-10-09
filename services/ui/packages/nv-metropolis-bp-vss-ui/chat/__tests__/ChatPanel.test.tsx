// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * End-to-end checks against the real SSE path: a fake `fetch` streams the
 * frames a backend would, and the assertions are on what a user sees.
 *
 * IndexedDB is mocked at the storage module rather than shimmed, because the
 * point here is the panel, not the persistence (covered in conversations.test).
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { copyToClipboard } from 'common';
import React from 'react';

import { ChatPanel } from '../lib-src/ChatPanel';
import { VssUiArtifact } from '../lib-src/markdown/components';
import { loadConversations, saveConversations } from '../lib-src/storage';
import { AGENT_RETRY_DELAY_MS } from '../lib-src/useChatStream';

jest.mock('common', () => ({
  ...jest.requireActual('common'),
  copyToClipboard: jest.fn().mockResolvedValue(true),
}));

jest.mock('../lib-src/storage', () => ({
  initConversationSessionLifecycle: jest.fn(),
  loadConversations: jest.fn().mockResolvedValue([]),
  loadSelectedConversationId: jest.fn().mockResolvedValue(null),
  loadChatExportAuxiliary: jest.fn(() => ({ folders: [], prompts: [] })),
  saveConversations: jest.fn().mockResolvedValue(undefined),
  saveSelectedConversationId: jest.fn().mockResolvedValue(undefined),
  saveChatExportAuxiliary: jest.fn(),
  clearAllConversations: jest.fn().mockResolvedValue(undefined),
}));

/** Build a Response whose body streams `chunks` as an SSE stream. */
function sseResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  let i = 0;
  return {
    ok: true,
    status: 200,
    body: {
      getReader: () => ({
        read: async () =>
          i < chunks.length
            ? { done: false, value: encoder.encode(chunks[i++]) }
            : { done: true, value: undefined },
        releaseLock: () => {},
      }),
    },
  } as unknown as Response;
}

function agentApiFrame(type: string, data: Record<string, unknown>, id: number): string {
  return `id: ${id}\nevent: ${type}\ndata: ${JSON.stringify({
    protocol_version: '1.0',
    id: String(id),
    type,
    run_id: 'run_1',
    thread_id: 'thread_1',
    data,
  })}\n\n`;
}

const endpoint = { url: '/api/vss-chat?surface=main' };
const noHeader = { headerMenu: false, uploadFile: false };
const withHitl = { ...noHeader, hitl: true };

async function typeAndSend(text: string) {
  const textarea = screen.getByTestId('chat-textarea');
  fireEvent.change(textarea, { target: { value: text } });
  fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
}

describe('ChatPanel', () => {
  afterEach(() => jest.restoreAllMocks());

  it('renders a validated inline raster image artifact', () => {
    render(
      <VssUiArtifact
        value={{
          version: '1.0',
          kind: 'vss.media.image',
          payload: {
            media_url: 'data:image/jpeg;base64,/9j/2Q==',
            alt: 'Warehouse snapshot',
          },
        }}
      />,
    );

    expect(screen.getByRole('img', { name: 'Warehouse snapshot' })).toHaveAttribute(
      'src',
      'data:image/jpeg;base64,/9j/2Q==',
    );
  });

  it('streams an answer and renders it as markdown', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      sseResponse([
        'data: {"choices":[{"delta":{"content":"**bold** "}}]}\n\n',
        'data: {"choices":[{"delta":{"content":"answer"}}]}\n\n',
        'data: [DONE]\n\n',
      ]),
    ) as any;

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);
    await act(async () => typeAndSend('what happened?'));

    await waitFor(() => expect(screen.getByText('bold')).toBeInTheDocument());
    // Rendered as markdown, not as literal asterisks.
    expect(screen.getByText('bold').tagName).toBe('STRONG');
    expect(screen.getByTestId('chat-message-user')).toHaveTextContent('what happened?');
  });

  it.each([
    { mediaProxyUrl: '/api/proxy', mediaPrefix: '/api/proxy' },
    { mediaProxyUrl: '/api/proxy///', mediaPrefix: '/api/proxy' },
    { mediaProxyUrl: undefined, mediaPrefix: '' },
  ])('opens NemoClaw Search media links with proxy $mediaProxyUrl', async ({ mediaProxyUrl, mediaPrefix }) => {
    const internal = 'http://host.openshell.internal:7777';
    const mediaPath = '/vst/api/v1/replay/stream/stream-1/picture?startTime=2026-09-29T12%3A00%3A00Z';
    const answer = [
      `[Media](${internal}${mediaPath})`,
      `[Frame](${internal}/vst/storage/frame.jpg?token=one)`,
      `Frame URL: ${internal}${mediaPath}`,
      '[Docs](https://example.com/vst/help)',
    ].join('\n\n');
    const fetchMock = jest.fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          run_id: 'run_1',
          events_url: '/api/agent/runs/run_1/events',
          cancel_url: '/api/agent/runs/run_1/cancel',
        }),
      })
      .mockResolvedValueOnce(sseResponse([
        agentApiFrame('run.started', {}, 1),
        agentApiFrame('message.delta', { delta: answer }, 2),
        agentApiFrame('run.completed', {}, 3),
      ]));
    global.fetch = fetchMock as any;

    render(
      <ChatPanel
        endpoint={{
          url: '/api/agent',
          transport: 'agent-api',
          conversationId: 'thread_1',
          mediaProxyUrl,
        }}
        features={noHeader}
      />,
    );
    await act(async () => typeAndSend('Find forklifts'));

    const media = await screen.findByRole('link', { name: 'Media' });
    expect(media).toHaveAttribute('href', `${mediaPrefix}${mediaPath}`);
    expect(screen.getByRole('link', { name: 'Frame' })).toHaveAttribute(
      'href', `${mediaPrefix}/vst/storage/frame.jpg?token=one`,
    );
    expect(screen.getByRole('link', { name: 'Docs' })).toHaveAttribute(
      'href', 'https://example.com/vst/help',
    );
    expect(screen.getByRole('link', { name: `${mediaPrefix}${mediaPath}` })).toHaveAttribute(
      'href', `${mediaPrefix}${mediaPath}`,
    );
    expect(screen.getByTestId('chat-message-assistant')).not.toHaveTextContent('host.openshell.internal');
  });

  it('preserves literal OpenShell URLs in code while rebasing rendered media', async () => {
    const mediaPath = '/vst/api/v1/replay/stream/stream-1/picture?startTime=2026-09-29T12%3A00%3A00Z';
    const url = `http://host.openshell.internal:7777${mediaPath}`;
    const command = `curl '${url}'`;
    const answer = [
      `Inline command URL: \`${url}\``,
      `\`\`\`bash\n${command}\n\`\`\``,
      `[Media](${url})`,
      `<a href="${url}">HTML media</a>`,
      `<img src="${url}" srcset="${url} 1x, ${url}&scale=2 2x, https://example.com/frame.jpg 3x" alt="Forklift frame" title="${url}" />`,
    ].join('\n\n');
    global.fetch = jest.fn().mockResolvedValue(sseResponse([
      `data: ${JSON.stringify({ choices: [{ delta: { content: answer } }] })}\n\n`,
      'data: [DONE]\n\n',
    ])) as any;

    render(<ChatPanel endpoint={{ ...endpoint, mediaProxyUrl: '/api/proxy' }} features={noHeader} />);
    await act(async () => typeAndSend('Show the frame and a command to fetch it'));

    expect(await screen.findByText(url, { selector: 'code' })).toBeInTheDocument();
    expect(screen.getByText(command, { selector: 'code' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Media' })).toHaveAttribute('href', `/api/proxy${mediaPath}`);
    expect(screen.getByRole('link', { name: 'HTML media' })).toHaveAttribute('href', `/api/proxy${mediaPath}`);
    expect(screen.getByRole('img', { name: 'Forklift frame' })).toHaveAttribute('src', `/api/proxy${mediaPath}`);
    expect(screen.getByRole('img', { name: 'Forklift frame' })).toHaveAttribute(
      'srcset', `/api/proxy${mediaPath} 1x, /api/proxy${mediaPath}&scale=2 2x, https://example.com/frame.jpg 3x`,
    );
    expect(screen.getByRole('img', { name: 'Forklift frame' })).toHaveAttribute('title', `/api/proxy${mediaPath}`);

    fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));
    await waitFor(() => expect(copyToClipboard).toHaveBeenCalledWith(command));
  });

  it('keeps earlier conversations visible and selectable after starting a new chat', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      sseResponse([
        'data: {"choices":[{"delta":{"content":"first answer"}}]}\n\n',
        'data: [DONE]\n\n',
      ]),
    ) as any;

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);
    await act(async () => typeAndSend('first question'));
    await waitFor(() => expect(screen.getByText('first answer')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Show conversation history' }));
    fireEvent.click(screen.getByRole('button', { name: 'New chat' }));

    expect(screen.queryByText('first answer')).not.toBeInTheDocument();
    const previousConversation = screen.getByRole('button', { name: 'first question' });
    expect(previousConversation).toBeInTheDocument();

    fireEvent.click(previousConversation);
    expect(screen.getByText('first answer')).toBeInTheDocument();
    expect(screen.getByTestId('chat-message-user')).toHaveTextContent('first question');
  });

  it('reflows a narrow chat panel when conversation history opens', () => {
    render(
      <div style={{ width: 380 }}>
        <ChatPanel endpoint={endpoint} features={noHeader} />
      </div>,
    );

    expect(screen.queryByRole('complementary', { name: 'Conversation history' })).not.toBeInTheDocument();
    expect(screen.getByTestId('chat-textarea')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show conversation history' }));
    const history = screen.getByRole('complementary', { name: 'Conversation history' });
    expect(history).toHaveClass('shrink-0', 'max-w-[calc(100%-3rem)]');
    expect(history).not.toHaveClass('absolute');

    fireEvent.click(screen.getByRole('button', { name: 'Hide conversation history' }));
    expect(screen.queryByRole('complementary', { name: 'Conversation history' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Show conversation history' })).toBeInTheDocument();
  });

  it('shows only one new-chat action while conversation history is open', () => {
    render(<ChatPanel endpoint={endpoint} />);

    expect(screen.getAllByRole('button', { name: 'New chat' })).toHaveLength(1);
    fireEvent.click(screen.getByRole('button', { name: 'Show conversation history' }));
    expect(screen.getAllByRole('button', { name: 'New chat' })).toHaveLength(1);
  });

  it('renders and answers interaction prompts', async () => {
    const interaction = {
      event_type: 'interaction_required',
      execution_id: 'execution-1',
      interaction_id: 'interaction-1',
      prompt: {
        text: 'Describe the scenario',
        input_type: 'text',
        placeholder: 'warehouse monitoring',
        required: true,
      },
      response_url: '/executions/execution-1/interactions/interaction-1/response',
    };
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(
        sseResponse([
          `event: interaction_required\ndata: ${JSON.stringify(interaction)}\n\n`,
          'data: {"choices":[{"delta":{"content":"started"}}]}\n\n',
          'data: [DONE]\n\n',
        ]),
      )
      .mockResolvedValueOnce({ ok: true, status: 204 });
    global.fetch = fetchMock as any;

    render(<ChatPanel endpoint={endpoint} features={withHitl} />);
    await act(async () => typeAndSend('start captioning'));

    await waitFor(() => expect(screen.getByTestId('hitl-modal')).toBeInTheDocument());
    expect(screen.getByTestId('hitl-modal-prompt')).toHaveTextContent('Describe the scenario');
    fireEvent.change(screen.getByTestId('hitl-modal-textarea'), {
      target: { value: 'warehouse monitoring' },
    });
    fireEvent.click(screen.getByTestId('hitl-modal-submit'));

    await waitFor(() => expect(screen.getByText('started')).toBeInTheDocument());
    expect(fetchMock.mock.calls[1][0]).toContain(
      'interaction=%2Fexecutions%2Fexecution-1%2Finteractions%2Finteraction-1%2Fresponse',
    );
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      response: { type: 'text', text: 'warehouse monitoring' },
    });
  });

  it('does not expose the legacy HITL response UI by default', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      sseResponse([
        `event: interaction_required\ndata: ${JSON.stringify({
          event_type: 'interaction_required',
          execution_id: 'execution-disabled',
          interaction_id: 'interaction-disabled',
          prompt: {
            text: 'This prompt must not be rendered',
            input_type: 'text',
            required: true,
          },
          response_url:
            '/executions/execution-disabled/interactions/interaction-disabled/response',
        })}\n\n`,
      ]),
    ) as any;

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);
    await act(async () => typeAndSend('ask through normal chat'));

    await waitFor(() =>
      expect(
        screen.getByText(/Interactive agent response UI is unavailable/),
      ).toBeInTheDocument(),
    );
    expect(screen.queryByTestId('hitl-modal')).not.toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledTimes(1);
  });

  it('submits an empty optional HITL confirmation with Enter', async () => {
    const interaction = {
      event_type: 'interaction_required',
      execution_id: 'execution-confirm',
      interaction_id: 'interaction-confirm',
      prompt: {
        text: 'Confirm these settings',
        input_type: 'text',
        required: false,
      },
      response_url: '/executions/execution-confirm/interactions/interaction-confirm/response',
    };
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(
        sseResponse([
          `event: interaction_required\ndata: ${JSON.stringify(interaction)}\n\n`,
          'data: {"choices":[{"delta":{"content":"confirmed"}}]}\n\n',
          'data: [DONE]\n\n',
        ]),
      )
      .mockResolvedValueOnce({ ok: true, status: 204 });
    global.fetch = fetchMock as any;

    render(<ChatPanel endpoint={endpoint} features={withHitl} />);
    await act(async () => typeAndSend('start captioning'));

    await waitFor(() => expect(screen.getByTestId('hitl-modal')).toBeInTheDocument());
    fireEvent.keyDown(screen.getByTestId('hitl-modal-textarea'), {
      key: 'Enter',
      shiftKey: false,
    });

    await waitFor(() => expect(screen.getByText('confirmed')).toBeInTheDocument());
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      response: { type: 'text', text: '' },
    });
  });

  it('keeps a pending prompt out of a conversation that did not ask for it', async () => {
    const interaction = {
      event_type: 'interaction_required',
      execution_id: 'execution-a',
      interaction_id: 'interaction-a',
      prompt: { text: 'Which aisle?', input_type: 'text', required: true },
      response_url: '/executions/execution-a/interactions/interaction-a/response',
    };
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(
        sseResponse([
          `event: interaction_required\ndata: ${JSON.stringify(interaction)}\n\n`,
          'data: {"choices":[{"delta":{"content":"resumed"}}]}\n\n',
          'data: [DONE]\n\n',
        ]),
      )
      .mockResolvedValueOnce({ ok: true, status: 204 });
    global.fetch = fetchMock as any;
    const onControlsReady = jest.fn();

    render(
      <ChatPanel endpoint={endpoint} features={withHitl} onControlsReady={onControlsReady} />,
    );
    await act(async () => typeAndSend('start captioning'));
    await waitFor(() => expect(screen.getByTestId('hitl-modal')).toBeInTheDocument());

    const controls = () => onControlsReady.mock.calls.at(-1)![0];
    const originatingId = controls().selectedConversationId;
    await act(async () => controls().onNewConversation());

    expect(controls().selectedConversationId).not.toBe(originatingId);
    expect(screen.queryByTestId('hitl-modal')).not.toBeInTheDocument();

    await act(async () => controls().onSelectConversation(originatingId));
    await waitFor(() => expect(screen.getByTestId('hitl-modal')).toBeInTheDocument());
    fireEvent.change(screen.getByTestId('hitl-modal-textarea'), { target: { value: 'aisle 4' } });
    await act(async () => {
      fireEvent.click(screen.getByTestId('hitl-modal-submit'));
    });

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toContain(
      'interaction=%2Fexecutions%2Fexecution-a%2Finteractions%2Finteraction-a%2Fresponse',
    );
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      response: { type: 'text', text: 'aisle 4' },
    });
  });

  it('declines a pending prompt when its conversation is deleted', async () => {
    const interaction = {
      event_type: 'interaction_required',
      execution_id: 'execution-gone',
      interaction_id: 'interaction-gone',
      prompt: { text: 'Which aisle?', input_type: 'text', required: true },
      response_url: '/executions/execution-gone/interactions/interaction-gone/response',
    };
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce(
        sseResponse([
          `event: interaction_required\ndata: ${JSON.stringify(interaction)}\n\n`,
          'data: [DONE]\n\n',
        ]),
      )
      .mockResolvedValueOnce({ ok: true, status: 204 });
    global.fetch = fetchMock as any;
    const onControlsReady = jest.fn();

    render(
      <ChatPanel endpoint={endpoint} features={withHitl} onControlsReady={onControlsReady} />,
    );
    await act(async () => typeAndSend('start captioning'));
    await waitFor(() => expect(screen.getByTestId('hitl-modal')).toBeInTheDocument());

    const controls = () => onControlsReady.mock.calls.at(-1)![0];
    await act(async () => controls().onDeleteConversation(controls().selectedConversationId));

    expect(screen.queryByTestId('hitl-modal')).not.toBeInTheDocument();
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual({
      response: { type: 'text', text: '/cancel' },
    });
  });

  it('walks a turn through a sequence of prompts one at a time', async () => {
    const prompts = [1, 2, 3, 4].map((n) => ({
      event_type: 'interaction_required',
      execution_id: 'execution-seq',
      interaction_id: `interaction-${n}`,
      prompt: { text: `Question ${n}`, input_type: 'text', required: true },
      response_url: `/executions/execution-seq/interactions/interaction-${n}/response`,
    }));
    const fetchMock = jest.fn().mockImplementation((url: unknown) =>
      String(url).includes('interaction=')
        ? Promise.resolve({ ok: true, status: 204 })
        : Promise.resolve(
            sseResponse([
              ...prompts.map(
                (request) => `event: interaction_required\ndata: ${JSON.stringify(request)}\n\n`,
              ),
              'data: {"choices":[{"delta":{"content":"all set"}}]}\n\n',
              'data: [DONE]\n\n',
            ]),
          ),
    );
    global.fetch = fetchMock as any;

    render(<ChatPanel endpoint={endpoint} features={withHitl} />);
    await act(async () => typeAndSend('walk me through it'));

    for (const request of prompts) {
      await waitFor(() =>
        expect(screen.getByTestId('hitl-modal-prompt')).toHaveTextContent(request.prompt.text),
      );
      // Each prompt waits for the previous answer: only one is ever on screen.
      expect(screen.getAllByTestId('hitl-modal')).toHaveLength(1);
      fireEvent.change(screen.getByTestId('hitl-modal-textarea'), {
        target: { value: `answer ${request.interaction_id}` },
      });
      await act(async () => {
        fireEvent.click(screen.getByTestId('hitl-modal-submit'));
      });
    }

    await waitFor(() => expect(screen.getByText('all set')).toBeInTheDocument());
    const answers = fetchMock.mock.calls.filter(([url]) => String(url).includes('interaction='));
    expect(answers.map(([url]) => String(url))).toEqual(
      prompts.map((request) =>
        `/api/vss-chat?surface=main&interaction=${encodeURIComponent(request.response_url)}`,
      ),
    );
    expect(answers.map(([, init]) => JSON.parse(init.body).response.text)).toEqual(
      prompts.map((request) => `answer ${request.interaction_id}`),
    );
  });

  it('sends the whole thread when chat history is on, and one turn when off', async () => {
    const fetchMock = jest.fn().mockResolvedValue(sseResponse(['data: [DONE]\n\n']));
    global.fetch = fetchMock as any;

    const { rerender } = render(
      <ChatPanel endpoint={endpoint} features={{ ...noHeader, chatHistory: false }} />,
    );
    await act(async () => typeAndSend('first'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.messages).toEqual([{ role: 'user', content: 'first' }]);
    rerender(<ChatPanel endpoint={endpoint} features={{ ...noHeader, chatHistory: false }} />);
  });

  it('reports the answer to the embedder with the conversation id', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      sseResponse(['data: {"choices":[{"delta":{"content":"done"}}]}\n\n', 'data: [DONE]\n\n']),
    ) as any;
    const onAnswer = jest.fn();
    const onSubmit = jest.fn();

    render(
      <ChatPanel
        endpoint={endpoint}
        features={noHeader}
        onAnswer={onAnswer}
        onSubmit={onSubmit}
      />,
    );
    await act(async () => typeAndSend('go'));

    await waitFor(() => expect(onAnswer).toHaveBeenCalled());
    expect(onSubmit).toHaveBeenCalledWith('go');
    const [answer, conversationId] = onAnswer.mock.calls[0];
    expect(answer).toBe('done');
    expect(typeof conversationId).toBe('string');
    expect(conversationId).not.toHaveLength(0);
  });

  it('signals completion before delivering the answer', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      sseResponse(['data: {"choices":[{"delta":{"content":"done"}}]}\n\n', 'data: [DONE]\n\n']),
    ) as any;
    const callbackOrder: string[] = [];

    render(
      <ChatPanel
        endpoint={endpoint}
        features={noHeader}
        onAnswerComplete={() => callbackOrder.push('complete')}
        onAnswer={() => {
          callbackOrder.push('answer');
        }}
      />,
    );
    await act(async () => typeAndSend('go'));

    await waitFor(() => expect(callbackOrder).toHaveLength(2));
    expect(callbackOrder).toEqual(['complete', 'answer']);
  });

  it('uses the structured agent API and delivers artifacts out of band', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          run_id: 'run_1',
          events_url: '/api/agent/runs/run_1/events',
          cancel_url: '/api/agent/runs/run_1/cancel',
        }),
      })
      .mockResolvedValueOnce(
        sseResponse([
          agentApiFrame('run.started', {}, 1),
          agentApiFrame('tool.started', { tool_call_id: 'tool_1', name: 'vss_search' }, 2),
          agentApiFrame('message.delta', { delta: 'found it' }, 3),
          agentApiFrame(
            'artifact.created',
            {
              version: '1.0',
              kind: 'vss.search.results',
              payload: { data: [{ video_name: 'clip.mp4' }] },
            },
            4,
          ),
          agentApiFrame('run.completed', {}, 5),
        ]),
      );
    global.fetch = fetchMock as any;
    const onAnswer = jest.fn();

    render(
      <ChatPanel
        endpoint={{
          url: '/api/agent',
          transport: 'agent-api',
          surface: 'vss-ui-main',
          conversationId: 'thread_1',
        }}
        features={noHeader}
        onAnswer={onAnswer}
      />,
    );
    await act(async () => typeAndSend('search the archive'));

    await waitFor(() => expect(screen.getByText('found it')).toBeInTheDocument());
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[0][0]).toBe('/api/agent/runs');
    const createBody = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(createBody).toMatchObject({
      input: [{ role: 'user', content: 'search the archive' }],
      surface: 'vss-ui-main',
    });
    expect(createBody.thread_id).toBe('thread_1');
    expect(fetchMock.mock.calls[1][0]).toBe('/api/agent/runs/run_1/events');
    expect(onAnswer.mock.calls[0][0]).toContain('<vss-ui-artifact>');
    expect(onAnswer.mock.calls[0][0]).toContain('vss.search.results');
  });

  it('renders a snapshot artifact with same-origin download support', async () => {
    const fetchMock = jest
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        status: 201,
        json: async () => ({
          run_id: 'run_1',
          events_url: '/api/agent/runs/run_1/events',
          cancel_url: '/api/agent/runs/run_1/cancel',
        }),
      })
      .mockResolvedValueOnce(
        sseResponse([
          agentApiFrame('run.started', {}, 1),
          agentApiFrame('message.delta', { delta: 'Snapshot ready.' }, 2),
          agentApiFrame(
            'artifact.created',
            {
              version: '1.0',
              kind: 'vss.media.image',
              payload: {
                media_url: '/vst/storage/temp/snapshot.jpg?token=one',
                alt: 'Snapshot of warehouse_safety_0001 at 0:05',
              },
            },
            3,
          ),
          agentApiFrame('run.completed', {}, 4),
        ]),
      );
    global.fetch = fetchMock as any;
    const onAnswer = jest.fn();

    render(
      <ChatPanel
        endpoint={{
          url: '/api/agent',
          transport: 'agent-api',
          surface: 'vss-ui-main',
          conversationId: 'thread_1',
          mediaProxyUrl: '/media',
        }}
        features={noHeader}
        onAnswer={onAnswer}
      />,
    );
    await act(async () => typeAndSend('take a snapshot'));

    const image = await screen.findByRole('img', {
      name: 'Snapshot of warehouse_safety_0001 at 0:05',
    });
    expect(image).toHaveAttribute('src', '/media/vst/storage/temp/snapshot.jpg?token=one');
    expect(screen.getByRole('button', { name: 'Download image' })).toBeInTheDocument();
    expect(onAnswer.mock.calls[0][0]).toContain('vss.media.image');
  });

  describe('agent API retry when the backend is unreachable', () => {
    const agentEndpoint = {
      url: '/api/agent',
      transport: 'agent-api' as const,
      surface: 'vss-ui-main',
      conversationId: 'thread_1',
    };
    const created = {
      ok: true,
      status: 201,
      json: async () => ({
        run_id: 'run_1',
        events_url: '/api/agent/runs/run_1/events',
        cancel_url: '/api/agent/runs/run_1/cancel',
      }),
    };
    const unreachable = {
      ok: false,
      status: 503,
      json: async () => ({ error: { code: 'backend_unreachable', message: 'OpenClaw Gateway is unreachable' } }),
    };
    const failedUnreachable = (id: number, markedUndelivered = true) =>
      agentApiFrame(
        'run.failed',
        {
          error: {
            code: 'backend_unreachable',
            message: 'OpenClaw Gateway is unreachable',
            retryable: true,
            ...(markedUndelivered ? { delivered: false } : {}),
          },
        },
        id,
      );
    const answered = () =>
      sseResponse([
        agentApiFrame('run.started', {}, 1),
        agentApiFrame('message.delta', { delta: 'answered' }, 2),
        agentApiFrame('run.completed', {}, 3),
      ]);
    const idempotencyKey = (fetchMock: jest.Mock, call: number) =>
      fetchMock.mock.calls[call][1].headers['Idempotency-Key'];

    beforeEach(() => jest.useFakeTimers());
    afterEach(() => jest.useRealTimers());

    const sendAndWaitOutRetry = async (text: string) => {
      await act(async () => typeAndSend(text));
      await act(async () => {
        await jest.advanceTimersByTimeAsync(AGENT_RETRY_DELAY_MS);
      });
    };

    it('retries once when the gateway is unreachable before the run is created', async () => {
      const fetchMock = jest
        .fn()
        .mockResolvedValueOnce(unreachable)
        .mockResolvedValueOnce(created)
        .mockResolvedValueOnce(answered());
      global.fetch = fetchMock as any;

      render(<ChatPanel endpoint={agentEndpoint} features={noHeader} />);
      await sendAndWaitOutRetry('hello');

      await waitFor(() => expect(screen.getByText('answered')).toBeInTheDocument());
      expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
        '/api/agent/runs',
        '/api/agent/runs',
        '/api/agent/runs/run_1/events',
      ]);
      expect(idempotencyKey(fetchMock, 1)).toBe(`${idempotencyKey(fetchMock, 0)}-retry`);
      expect(screen.queryByText(/HTTP 503/)).not.toBeInTheDocument();
    });

    it('retries once when the run fails before reaching the agent', async () => {
      const fetchMock = jest
        .fn()
        .mockResolvedValueOnce(created)
        .mockResolvedValueOnce(sseResponse([agentApiFrame('run.started', {}, 1), failedUnreachable(2)]))
        .mockResolvedValueOnce(created)
        .mockResolvedValueOnce(answered());
      global.fetch = fetchMock as any;

      render(<ChatPanel endpoint={agentEndpoint} features={noHeader} />);
      await sendAndWaitOutRetry('hello');

      await waitFor(() => expect(screen.getByText('answered')).toBeInTheDocument());
      expect(fetchMock).toHaveBeenCalledTimes(4);
      expect(idempotencyKey(fetchMock, 2)).toBe(`${idempotencyKey(fetchMock, 0)}-retry`);
      expect(screen.queryByText(/OpenClaw Gateway is unreachable/)).not.toBeInTheDocument();
    });

    it('does not retry a failure the adapter cannot place before delivery', async () => {
      const fetchMock = jest
        .fn()
        .mockResolvedValueOnce(created)
        .mockResolvedValueOnce(sseResponse([agentApiFrame('run.started', {}, 1), failedUnreachable(2, false)]));
      global.fetch = fetchMock as any;

      render(<ChatPanel endpoint={agentEndpoint} features={noHeader} />);
      await sendAndWaitOutRetry('hello');

      await waitFor(() => expect(screen.getByText(/OpenClaw Gateway is unreachable/)).toBeInTheDocument());
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('does not retry once the agent has produced output', async () => {
      const fetchMock = jest
        .fn()
        .mockResolvedValueOnce(created)
        .mockResolvedValueOnce(
          sseResponse([
            agentApiFrame('run.started', {}, 1),
            agentApiFrame('message.delta', { delta: 'partial' }, 2),
            failedUnreachable(3),
          ]),
        );
      global.fetch = fetchMock as any;

      render(<ChatPanel endpoint={agentEndpoint} features={noHeader} />);
      await sendAndWaitOutRetry('hello');

      await waitFor(() => expect(screen.getByText(/OpenClaw Gateway is unreachable/)).toBeInTheDocument());
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });

    it('reports the failure after a single retry', async () => {
      const fetchMock = jest.fn().mockResolvedValue(unreachable);
      global.fetch = fetchMock as any;
      const onAuthFailure = jest.fn();

      render(<ChatPanel endpoint={agentEndpoint} features={noHeader} onAuthFailure={onAuthFailure} />);
      await sendAndWaitOutRetry('hello');

      await waitFor(() => expect(screen.getByText(/HTTP 503/)).toBeInTheDocument());
      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(onAuthFailure).not.toHaveBeenCalled();
    });
  });

  describe('agent API credentials rejected', () => {
    const agentEndpoint = {
      url: '/api/agent',
      transport: 'agent-api' as const,
      surface: 'vss-ui-main',
      conversationId: 'thread_1',
    };

    it('reports a 401 from run creation to the embedder after showing it', async () => {
      global.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ error: { code: 'backend_auth_error' } }),
      }) as any;
      const onAuthFailure = jest.fn(() => {
        expect(screen.getByText(/HTTP 401/)).toBeInTheDocument();
      });

      render(<ChatPanel endpoint={agentEndpoint} features={noHeader} onAuthFailure={onAuthFailure} />);
      await act(async () => typeAndSend('hello'));

      await waitFor(() => expect(onAuthFailure).toHaveBeenCalledTimes(1));
      expect(global.fetch).toHaveBeenCalledTimes(1);
    });

    it('saves the failed turn when the embedder unmounts the panel to ask for a token', async () => {
      global.fetch = jest.fn().mockResolvedValue({
        ok: false,
        status: 401,
        json: async () => ({ error: { code: 'backend_auth_error' } }),
      }) as any;
      const save = saveConversations as jest.Mock;
      function Embedder() {
        const [rejected, setRejected] = React.useState(false);
        return rejected ? (
          <p>token prompt</p>
        ) : (
          <ChatPanel endpoint={agentEndpoint} features={noHeader} onAuthFailure={() => setRejected(true)} />
        );
      }

      render(<Embedder />);
      await act(async () => {});
      save.mockClear();
      await act(async () => typeAndSend('resend me later'));

      expect(await screen.findByText('token prompt')).toBeInTheDocument();
      const saved = JSON.stringify(save.mock.calls[save.mock.calls.length - 1][0]);
      expect(saved).toContain('resend me later');
      expect(saved).toContain('HTTP 401');
    });

    it('reports a run the gateway rejected', async () => {
      global.fetch = jest
        .fn()
        .mockResolvedValueOnce({
          ok: true,
          status: 201,
          json: async () => ({
            run_id: 'run_1',
            events_url: '/api/agent/runs/run_1/events',
            cancel_url: '/api/agent/runs/run_1/cancel',
          }),
        })
        .mockResolvedValueOnce(
          sseResponse([
            agentApiFrame('run.started', {}, 1),
            agentApiFrame(
              'run.failed',
              { error: { code: 'backend_auth_error', message: 'OpenClaw Gateway authentication failed' } },
              2,
            ),
          ]),
        ) as any;
      const onAuthFailure = jest.fn();

      render(<ChatPanel endpoint={agentEndpoint} features={noHeader} onAuthFailure={onAuthFailure} />);
      await act(async () => typeAndSend('hello'));

      await waitFor(() => expect(onAuthFailure).toHaveBeenCalledTimes(1));
      expect(screen.getByText(/OpenClaw Gateway authentication failed/)).toBeInTheDocument();
      expect(global.fetch).toHaveBeenCalledTimes(2);
    });
  });

  it('saves a turn that is still pending when the panel unmounts', async () => {
    global.fetch = jest.fn(() => new Promise<Response>(() => {})) as any;
    const save = saveConversations as jest.Mock;

    const { unmount } = render(<ChatPanel endpoint={endpoint} features={noHeader} />);
    await act(async () => {});
    save.mockClear();
    await act(async () => typeAndSend('keep this message'));
    expect(save).not.toHaveBeenCalled();

    unmount();

    expect(save).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(save.mock.calls[0][0])).toContain('keep this message');
  });

  it('folds a context chip into the request and clears it after sending', async () => {
    const fetchMock = jest.fn().mockResolvedValue(sseResponse(['data: [DONE]\n\n']));
    global.fetch = fetchMock as any;

    let addContext: ((item: any) => void) | undefined;
    render(
      <ChatPanel
        endpoint={endpoint}
        features={noHeader}
        onAddQueryContextReady={(add) => {
          addContext = add;
        }}
      />,
    );

    await act(async () => {
      addContext?.({
        id: 'chip1',
        label: 'Camera 3',
        contextType: 'media/video',
        data: { videoId: 'v3' },
      });
    });
    expect(screen.getByText('Camera 3')).toBeInTheDocument();

    await act(async () => typeAndSend('summarise'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    const sent = body.messages[body.messages.length - 1].content;
    expect(sent).toContain('[Context: [{"videoId":"v3"}]]');
    expect(sent).toContain('summarise');
    // Chips apply to one turn only.
    await waitFor(() => expect(screen.queryByText('Camera 3')).not.toBeInTheDocument());
  });

  it.each([
    ['chat-sse', 'rtsp'],
    ['chat-sse', 'video_file'],
    ['agent-api', 'rtsp'],
    ['agent-api', 'video_file'],
  ] as const)('regenerates %s searches with the original %s parameters', async (transport, sourceType) => {
    sessionStorage.removeItem('vss-chat-custom-agent-params');
    const fetchMock = jest.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('/runs')) {
        return {
          ok: true,
          json: async () => ({
            run_id: 'run_1',
            events_url: '/api/agent/runs/run_1/events',
            cancel_url: '/api/agent/runs/run_1/cancel',
          }),
        };
      }
      return transport === 'agent-api'
        ? sseResponse([agentApiFrame('run.completed', {}, 1)])
        : sseResponse(['data: [DONE]\n\n']);
    });
    global.fetch = fetchMock as any;
    const configuredEndpoint = {
      url: transport === 'agent-api' ? '/api/agent' : endpoint.url,
      transport,
      conversationId: 'thread_1',
      extraParams: { top_k: 5, search_source_type: 'video_file', use_critic: true },
    };
    const customAgentParamsJson = JSON.stringify({
      params: [
        {
          name: 'search_source_type',
          label: 'Search media source type',
          type: 'select',
          'default-value': 'video_file',
          options: ['video_file', 'rtsp'],
        },
        { name: 'use_critic', label: 'Enable Critic', type: 'boolean', 'default-value': true },
      ],
    });
    const { rerender } = render(
      <ChatPanel endpoint={configuredEndpoint} features={noHeader} customAgentParamsJson={customAgentParamsJson} />,
    );

    fireEvent.click(await screen.findByRole('button', { name: 'Agent parameters' }));
    fireEvent.change(screen.getByLabelText('Search media source type'), { target: { value: sourceType } });
    fireEvent.click(screen.getByRole('switch'));
    await act(async () => typeAndSend('find forklifts'));

    const requestBodies = () => fetchMock.mock.calls
      .filter(([, init]) => init?.method === 'POST')
      .map(([, init]) => JSON.parse(init.body));
    const original = requestBodies()[0];
    expect(transport === 'agent-api' ? original.metadata : original).toMatchObject({
      search_source_type: sourceType,
      use_critic: false,
      top_k: 5,
    });

    // Changing controls and endpoint defaults should apply to future turns.
    fireEvent.click(screen.getByRole('button', { name: 'Agent parameters' }));
    fireEvent.change(screen.getByLabelText('Search media source type'), {
      target: { value: sourceType === 'rtsp' ? 'video_file' : 'rtsp' },
    });
    fireEvent.click(screen.getByRole('switch'));
    rerender(
      <ChatPanel
        endpoint={{ ...configuredEndpoint, extraParams: { ...configuredEndpoint.extraParams, top_k: 10, threshold: 0.4 } }}
        features={noHeader}
        customAgentParamsJson={customAgentParamsJson}
      />,
    );

    for (let repeat = 0; repeat < 2; repeat += 1) {
      await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Regenerate response' })));
      expect(requestBodies()[repeat + 1]).toEqual(original);
      expect(screen.getAllByTestId('chat-message-user')).toHaveLength(1);
    }
  });

  it('regenerates a saved turn with its parameters after remounting', async () => {
    const fetchMock = jest.fn().mockImplementation(async () => sseResponse(['data: [DONE]\n\n']));
    global.fetch = fetchMock as any;
    const originalParams = { search_source_type: 'rtsp', use_critic: false };
    const { unmount } = render(
      <ChatPanel endpoint={{ ...endpoint, extraParams: originalParams }} features={noHeader} />,
    );
    await act(async () => typeAndSend('find forklifts'));
    unmount();

    const saved = jest.mocked(saveConversations).mock.calls.at(-1)![0];
    jest.mocked(loadConversations).mockResolvedValueOnce(saved);
    render(
      <ChatPanel
        endpoint={{ ...endpoint, extraParams: { search_source_type: 'video_file', use_critic: true, top_k: 10 } }}
        features={noHeader}
      />,
    );
    await screen.findByTestId('chat-message-user');
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Regenerate response' })));

    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toEqual(JSON.parse(fetchMock.mock.calls[0][1].body));
  });

  it.each([
    { label: 'saved empty parameters', params: {}, expected: {} },
    { label: 'legacy messages without saved parameters', params: undefined, expected: { search_source_type: 'rtsp', top_k: 10 } },
  ])('regenerates $label with the appropriate defaults', async ({ params, expected }) => {
    const fetchMock = jest.fn().mockImplementation(async () => sseResponse(['data: [DONE]\n\n']));
    global.fetch = fetchMock as any;
    jest.mocked(loadConversations).mockResolvedValueOnce([{
      id: 'saved-thread',
      name: 'Saved search',
      messages: [
        { id: 'saved-user', role: 'user', content: 'find forklifts', params },
        { id: 'saved-answer', role: 'assistant', content: 'Original results' },
      ],
    }]);
    render(
      <ChatPanel
        endpoint={{ ...endpoint, extraParams: { search_source_type: 'rtsp', top_k: 10 } }}
        features={noHeader}
      />,
    );
    await screen.findByTestId('chat-message-user');
    await act(async () => fireEvent.click(screen.getByRole('button', { name: 'Regenerate response' })));

    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({
      ...expected,
      messages: [{ role: 'user', content: 'find forklifts' }],
    });
  });

  it('lets an embedder submit a message without the user typing', async () => {
    const fetchMock = jest.fn().mockResolvedValue(sseResponse(['data: [DONE]\n\n']));
    global.fetch = fetchMock as any;

    let submit: ((message: string) => void) | undefined;
    const onMessageSubmitted = jest.fn();
    render(
      <ChatPanel
        endpoint={endpoint}
        features={noHeader}
        onSubmitMessageReady={(fn) => {
          submit = fn;
        }}
        onMessageSubmitted={onMessageSubmitted}
      />,
    );

    await act(async () => submit?.('generate a report'));
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(onMessageSubmitted).toHaveBeenCalled();
    expect(screen.getByTestId('chat-message-user')).toHaveTextContent('generate a report');
  });

  it('shows an HTTP failure on the message instead of failing silently', async () => {
    global.fetch = jest.fn().mockResolvedValue({ ok: false, status: 502 } as Response) as any;

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);
    await act(async () => typeAndSend('hello'));

    await waitFor(() => expect(screen.getByText(/HTTP 502/)).toBeInTheDocument());
  });

  it('renders intermediate steps as a nested tree', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      sseResponse([
        'intermediate_data: {"id":"1","name":"vss-search-archive","status":"complete"}\n',
        'intermediate_data: {"id":"2","name":"fetch-clip","parent_id":"1","status":"complete"}\n',
        'data: {"choices":[{"delta":{"content":"found it"}}]}\n\n',
        'data: [DONE]\n\n',
      ]),
    ) as any;

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);
    await act(async () => typeAndSend('search'));

    await waitFor(() => expect(screen.getByText(/Intermediate steps \(2\)/)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Intermediate steps \(2\)/));
    expect(screen.getByText('vss-search-archive')).toBeInTheDocument();
  });

  it('settles an unfinished step when the stream dies mid-turn', async () => {
    const encoder = new TextEncoder();
    let reads = 0;
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      body: {
        getReader: () => ({
          read: async () => {
            if (reads++ > 0) throw new Error('stream died');
            return {
              done: false,
              value: encoder.encode(
                'intermediate_data: {"id":"1","name":"vss-search-archive","status":"in_progress"}\n',
              ),
            };
          },
          releaseLock: () => {},
        }),
      },
    } as unknown as Response) as any;

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);
    await act(async () => typeAndSend('search'));

    await waitFor(() => expect(screen.getByText(/stream died/)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Intermediate steps \(1\)/));
    expect(screen.getByText('vss-search-archive').closest('li')).toHaveAttribute(
      'data-status',
      'error',
    );
  });

  it('reports a clean EOF without DONE as an interrupted response', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      sseResponse([
        'intermediate_data: {"id":"1","name":"vss-search-archive","status":"in_progress"}\n',
        'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n',
      ]),
    ) as any;

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);
    await act(async () => typeAndSend('search'));

    await waitFor(() =>
      expect(
        screen.getByText(/backend event stream ended before the response completed/),
      ).toBeInTheDocument(),
    );
    fireEvent.click(screen.getByText(/Intermediate steps \(1\)/));
    expect(screen.getByText('vss-search-archive').closest('li')).toHaveAttribute(
      'data-status',
      'error',
    );
  });

  it('keeps workflow children visible when a start frame is replaced by completion', async () => {
    global.fetch = jest.fn().mockResolvedValue(
      sseResponse([
        'intermediate_data: {"id":"workflow","name":"Function Start: <workflow>","parent_id":"root"}\n',
        'intermediate_data: {"id":"model","name":"nvidia/model","parent_id":"workflow"}\n',
        'intermediate_data: {"id":"workflow","name":"Function Complete: <workflow>","parent_id":"root","status":"complete"}\n',
        'data: {"choices":[{"delta":{"content":"done"}}]}\n\n',
        'data: [DONE]\n\n',
      ]),
    ) as any;

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);
    await act(async () => typeAndSend('run'));

    await waitFor(() => expect(screen.getByText(/Intermediate steps \(1\)/)).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Intermediate steps \(1\)/));
    expect(screen.getByText('nvidia/model')).toBeInTheDocument();
    expect(screen.queryByText(/Function (?:Start|Complete): <workflow>/)).not.toBeInTheDocument();
  });

  it('notifies the embedder when a turn starts and ends', async () => {
    global.fetch = jest.fn().mockResolvedValue(sseResponse(['data: [DONE]\n\n'])) as any;
    const onBusyChange = jest.fn();

    render(<ChatPanel endpoint={endpoint} features={noHeader} onBusyChange={onBusyChange} />);
    await act(async () => typeAndSend('go'));

    await waitFor(() => expect(onBusyChange).toHaveBeenCalledWith(true));
    await waitFor(() => expect(onBusyChange).toHaveBeenLastCalledWith(false));
  });

  it('deletes the message the button belongs to, not the one at that position', async () => {
    global.fetch = jest
      .fn()
      .mockImplementation(() =>
        Promise.resolve(
          sseResponse(['data: {"choices":[{"delta":{"content":"ok"}}]}\n\n', 'data: [DONE]\n\n']),
        ),
      ) as any;

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);

    await act(async () => typeAndSend('first question'));
    await waitFor(() => expect(screen.getAllByTestId('chat-message-assistant')).toHaveLength(1));
    await act(async () => typeAndSend('second question'));
    await waitFor(() => expect(screen.getAllByTestId('chat-message-assistant')).toHaveLength(2));

    // One delete button per user turn; the first belongs to 'first question'.
    fireEvent.click(screen.getAllByLabelText('Delete message')[0]);

    await waitFor(() =>
      expect(within(screen.getByRole('log')).queryByText('first question')).not.toBeInTheDocument(),
    );
    // The other turn is untouched — deletion addressed a message, not a slot.
    expect(within(screen.getByRole('log')).getByText('second question')).toBeInTheDocument();
  });

  it('hands conversation controls to the host exactly once per meaningful change', async () => {
    global.fetch = jest.fn().mockResolvedValue(sseResponse(['data: [DONE]\n\n'])) as any;
    const onControlsReady = jest.fn();

    render(
      <ChatPanel endpoint={endpoint} features={noHeader} onControlsReady={onControlsReady} />,
    );
    await waitFor(() => expect(onControlsReady).toHaveBeenCalled());

    const handlers = () => onControlsReady.mock.calls.at(-1)![0];
    expect(handlers().filteredConversations).toHaveLength(1);
    expect(handlers().folders).toEqual([]);
    expect(typeof handlers().onNewConversation).toBe('function');
    expect(typeof handlers().onCreateFolder).toBe('function');
    expect(typeof handlers().onMoveConversation).toBe('function');
    expect(handlers().busy).toBe(false);

    const conversationId = handlers().selectedConversationId;
    let folder!: { id: string; name: string };
    await act(async () => {
      folder = handlers().onCreateFolder();
    });
    await waitFor(() => expect(handlers().folders).toContainEqual(folder));

    await act(async () => handlers().onMoveConversation(conversationId, folder.id));
    await waitFor(() =>
      expect(handlers().conversations.find((item: any) => item.id === conversationId)?.folderId)
        .toBe(folder.id),
    );

    await act(async () => handlers().onDeleteFolder(folder.id));
    await waitFor(() => expect(handlers().folders).toEqual([]));
    expect(handlers().conversations.find((item: any) => item.id === conversationId)?.folderId)
      .toBeNull();
  });
});
