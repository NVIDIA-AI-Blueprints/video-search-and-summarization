// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
/**
 * End-to-end checks against the real SSE path: a fake `fetch` streams the
 * frames a backend would, and the assertions are on what a user sees.
 *
 * IndexedDB is mocked at the storage module rather than shimmed, because the
 * point here is the panel, not the persistence (covered in conversations.test).
 */
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';

import { ChatPanel } from '../lib-src/ChatPanel';

jest.mock('../lib-src/storage', () => ({
  initConversationSessionLifecycle: jest.fn(),
  loadConversations: jest.fn().mockResolvedValue([]),
  loadSelectedConversationId: jest.fn().mockResolvedValue(null),
  saveConversations: jest.fn().mockResolvedValue(undefined),
  saveSelectedConversationId: jest.fn().mockResolvedValue(undefined),
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

async function typeAndSend(text: string) {
  const textarea = screen.getByTestId('chat-textarea');
  fireEvent.change(textarea, { target: { value: text } });
  fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
}

describe('ChatPanel', () => {
  afterEach(() => jest.restoreAllMocks());

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

  it('renders and answers NAT interaction prompts', async () => {
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

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);
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

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);
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
      <ChatPanel endpoint={endpoint} features={noHeader} onControlsReady={onControlsReady} />,
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
      <ChatPanel endpoint={endpoint} features={noHeader} onControlsReady={onControlsReady} />,
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

    render(<ChatPanel endpoint={endpoint} features={noHeader} />);
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
      expect(screen.queryByText('first question')).not.toBeInTheDocument(),
    );
    // The other turn is untouched — deletion addressed a message, not a slot.
    expect(screen.getByText('second question')).toBeInTheDocument();
  });

  it('hands conversation controls to the host exactly once per meaningful change', async () => {
    global.fetch = jest.fn().mockResolvedValue(sseResponse(['data: [DONE]\n\n'])) as any;
    const onControlsReady = jest.fn();

    render(
      <ChatPanel endpoint={endpoint} features={noHeader} onControlsReady={onControlsReady} />,
    );
    await waitFor(() => expect(onControlsReady).toHaveBeenCalled());

    const handlers = onControlsReady.mock.calls.at(-1)![0];
    expect(handlers.filteredConversations).toHaveLength(1);
    expect(typeof handlers.onNewConversation).toBe('function');
    expect(handlers.busy).toBe(false);
  });
});
