// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import EventQuestions from '../../components/EventQuestions';

const turn = {
  id: 'request-1', event_id: 'event-one', question: 'Is a collision visible?',
  answer: 'No collision is visible in this clip. The cause of the queue is outside the camera view.',
  created_at: 100, clip_sha256: 'exact-video-hash',
};
const response = (data: unknown, ok = true) => ({ ok, json: async () => data });
const deferred = () => {
  let resolve!: (value: any) => void;
  const promise = new Promise<any>(done => { resolve = done; });
  return { promise, resolve };
};
const composer = () => screen.getByLabelText('Question about attached clip');
const send = () => screen.getByRole('button', { name: /^(Send|Retry) question$/ });
const posts = () => (global.fetch as jest.Mock).mock.calls.filter(call => call[1]?.method === 'POST');
const submit = (question = turn.question) => {
  fireEvent.change(composer(), { target: { value: question } });
  fireEvent.click(send());
};

describe('Attached video questions', () => {
  const originalFetch = global.fetch;
  const originalResizeObserver = global.ResizeObserver;
  beforeEach(() => {
    global.fetch = jest.fn().mockImplementation(async (_url, options) => response(options?.method === 'POST' ? turn : [])) as any;
    Object.defineProperty(global.crypto, 'randomUUID', { configurable: true, value: () => 'request-1' });
  });
  afterEach(() => { global.fetch = originalFetch; global.ResizeObserver = originalResizeObserver; jest.useRealTimers(); });
  const load = async () => {
    const view = render(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" />);
    await screen.findByText('Ask about what is visible in the attached clip.');
    return view;
  };

  it('loads history without calling the model or stealing focus when a clip is attached', async () => {
    await load();
    expect(posts()).toHaveLength(0);
    expect(composer()).not.toHaveFocus();
    expect(screen.getByText(/Saved clip · 3:00 PM. Off-camera events may be unknown./)).toBeInTheDocument();
    expect(send()).toBeDisabled();
  });

  it('sends the question to the attached event and presents its video-grounded answer', async () => {
    await load();
    submit();
    expect(await screen.findByText(turn.answer)).toBeInTheDocument();
    expect(global.fetch).toHaveBeenCalledWith('/api/d1/reviews/event-one/questions', expect.objectContaining({
      method: 'POST', body: JSON.stringify({ question: turn.question, request_id: 'request-1' }),
    }));
    expect(composer()).toHaveValue('');
  });

  it('only fills suggestions instead of automatically calling the model', async () => {
    await load();
    fireEvent.click(screen.getByRole('button', { name: 'Is a collision visible?' }));
    expect(composer()).toHaveValue('Is a collision visible in this clip?');
    expect(composer()).toHaveFocus();
    expect(posts()).toHaveLength(0);
  });

  it('rejects an answer for another event rather than attaching it to the current video', async () => {
    (global.fetch as jest.Mock).mockImplementation(async (_url, options) => response(options?.method === 'POST' ? { ...turn, event_id: 'wrong-event' } : []));
    await load();
    submit();
    expect(await screen.findByRole('alert')).toHaveTextContent('answer did not match this clip');
    expect(screen.queryByText(turn.answer)).not.toBeInTheDocument();
    expect(composer()).toHaveValue(turn.question);
  });

  it('keeps the question and reuses the request ID after a failed attempt', async () => {
    let attempts = 0;
    (global.fetch as jest.Mock).mockImplementation(async (_url, options) => options?.method === 'POST' && attempts++ === 0
      ? response({ error: 'Cosmos is busy' }, false) : response(options?.method === 'POST' ? turn : []));
    await load();
    submit();
    expect(await screen.findByRole('alert')).toHaveTextContent('Cosmos is busy');
    expect(composer()).toHaveValue(turn.question);
    fireEvent.click(send());
    await screen.findByText(turn.answer);
    expect(posts()).toHaveLength(2);
    expect(posts()[0][1].body).toBe(posts()[1][1].body);
  });

  it('never shows saved questions from another clip', async () => {
    (global.fetch as jest.Mock).mockResolvedValue(response([{ ...turn, event_id: 'other-event' }]));
    await load();
    expect(screen.queryByText(turn.answer)).not.toBeInTheDocument();
  });

  it('does not allow questions until the exact evidence clip is ready', async () => {
    render(<EventQuestions eventId="pending" clipReady={false} clipTime="3:00 PM" />);
    await screen.findByText('Ask about what is visible in the attached clip.');
    expect(composer()).toBeDisabled();
    expect(send()).toBeDisabled();
  });

  it('keeps the draft editable but prevents sending while another clip is being answered', async () => {
    const view = render(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" busyElsewhere />);
    await screen.findByText('Ask about what is visible in the attached clip.');
    expect(screen.getByRole('status')).toHaveTextContent('Another clip has a pending question');
    expect(composer()).not.toBeDisabled();
    fireEvent.change(composer(), { target: { value: turn.question } });
    expect(send()).toBeDisabled();
    fireEvent.submit(composer().closest('form')!);
    expect(posts()).toHaveLength(0);
    view.rerender(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" busyElsewhere={false} />);
    expect(composer()).toHaveValue(turn.question);
    expect(send()).not.toBeDisabled();
    fireEvent.click(send());
    expect(await screen.findByText(turn.answer)).toBeInTheDocument();
  });

  it('retains a draft while its thread is inactive and only focuses on an active attachment request', async () => {
    const view = render(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" active={false} focusRequest={1} />);
    await screen.findByText('Ask about what is visible in the attached clip.');
    expect(composer()).not.toHaveFocus();
    fireEvent.change(composer(), { target: { value: 'Which lane is blocked?' } });
    view.rerender(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" active focusRequest={1} />);
    expect(composer()).toHaveFocus();
    expect(composer()).toHaveValue('Which lane is blocked?');
    send().focus();
    expect(composer()).not.toHaveFocus();
    view.rerender(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" active focusRequest={2} />);
    expect(composer()).toHaveFocus();
    expect(posts()).toHaveLength(0);
  });

  it('keeps answering an inactive thread, reports pending changes, and scrolls only when active', async () => {
    const answer = deferred();
    (global.fetch as jest.Mock).mockImplementation((_url, options) => options?.method === 'POST' ? answer.promise : Promise.resolve(response([])));
    const onPendingChange = jest.fn();
    const view = render(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" onPendingChange={onPendingChange} />);
    await screen.findByText('Ask about what is visible in the attached clip.');
    const history = view.container.querySelector('[data-chat-conversation]') as HTMLDivElement;
    Object.defineProperty(history, 'scrollHeight', { configurable: true, value: 500 });
    submit();
    expect(onPendingChange).toHaveBeenLastCalledWith('event-one', true);
    expect(composer()).toBeDisabled();
    expect(history.scrollTop).toBe(500);
    view.rerender(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" onPendingChange={onPendingChange} active={false} />);
    history.scrollTop = 17;
    await act(async () => { answer.resolve(response(turn)); });
    expect(await screen.findByText(turn.answer)).toBeInTheDocument();
    expect(onPendingChange).toHaveBeenLastCalledWith('event-one', false);
    expect(history.scrollTop).toBe(17);
    view.rerender(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" onPendingChange={onPendingChange} active />);
    expect(history.scrollTop).toBe(500);
    expect(posts()).toHaveLength(1);
  });

  it('follows the newest answer on resize only when the user was near the bottom', async () => {
    const observers: Array<{ resize: () => void; disconnect: jest.Mock }> = [];
    global.ResizeObserver = class {
      item: typeof observers[number];
      constructor(callback: ResizeObserverCallback) {
        this.item = { resize: () => callback([], this as unknown as ResizeObserver), disconnect: jest.fn() };
        observers.push(this.item);
      }
      observe() {}
      unobserve() {}
      disconnect() { this.item.disconnect(); }
    } as unknown as typeof ResizeObserver;
    const view = await load();
    const history = view.container.querySelector('[data-chat-conversation]') as HTMLDivElement;
    let viewportHeight = 300;
    let position = 590;
    Object.defineProperties(history, {
      scrollHeight: { configurable: true, get: () => 900 },
      clientHeight: { configurable: true, get: () => viewportHeight },
      scrollTop: { configurable: true, get: () => position, set: value => { position = Math.max(0, Math.min(value, 900 - viewportHeight)); } },
    });
    fireEvent.scroll(history); // Within 10px of the bottom before shrinking.
    viewportHeight = 200;
    act(() => observers[0].resize());
    expect(history.scrollTop).toBe(700);
    history.scrollTop = 100;
    fireEvent.scroll(history); // The user is now reading an older answer.
    viewportHeight = 150;
    act(() => observers[0].resize());
    expect(history.scrollTop).toBe(100);

    view.rerender(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" active={false} />);
    expect(observers[0].disconnect).toHaveBeenCalledTimes(1);
    history.scrollTop = 750;
    fireEvent.scroll(history);
    viewportHeight = 100;
    act(() => observers[0].resize()); // A stale callback after disconnect must do nothing.
    expect(history.scrollTop).toBe(750);
    view.rerender(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" active />);
    expect(history.scrollTop).toBe(800);
    view.unmount();
    expect(observers[1].disconnect).toHaveBeenCalledTimes(1);
    viewportHeight = 50;
    act(() => observers[1].resize());
    expect(history.scrollTop).toBe(800);
  });

  it('aborts pending work and releases its busy state when a thread is removed', async () => {
    const answer = deferred();
    (global.fetch as jest.Mock).mockImplementation((_url, options) => options?.method === 'POST' ? answer.promise : Promise.resolve(response([])));
    const onPendingChange = jest.fn();
    const view = render(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" onPendingChange={onPendingChange} />);
    await screen.findByText('Ask about what is visible in the attached clip.');
    submit();
    const signal = posts()[0][1].signal as AbortSignal;
    expect(signal.aborted).toBe(false);
    view.unmount();
    expect(signal.aborted).toBe(true);
    expect(onPendingChange.mock.calls).toEqual([['event-one', true], ['event-one', false]]);
    await act(async () => { answer.resolve(response(turn)); });
    expect(onPendingChange.mock.calls).toEqual([['event-one', true], ['event-one', false]]);
  });

  it('cannot place a late answer on a replacement clip or clear the replacement request', async () => {
    const oldAnswer = deferred();
    const newAnswer = deferred();
    (global.fetch as jest.Mock).mockImplementation((url, options) => options?.method !== 'POST'
      ? Promise.resolve(response([])) : String(url).includes('event-one') ? oldAnswer.promise : newAnswer.promise);
    const onPendingChange = jest.fn();
    const view = render(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" onPendingChange={onPendingChange} />);
    await screen.findByText('Ask about what is visible in the attached clip.');
    submit();
    view.rerender(<EventQuestions eventId="event-two" clipReady clipTime="3:01 PM" onPendingChange={onPendingChange} />);
    await screen.findByText('Ask about what is visible in the attached clip.');
    expect(posts()[0][1].signal.aborted).toBe(true);
    expect(composer()).toHaveValue('');
    submit('Is the ramp moving?');
    expect(onPendingChange).toHaveBeenLastCalledWith('event-two', true);
    await act(async () => { oldAnswer.resolve(response(turn)); });
    expect(screen.queryByText(turn.answer)).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Sending your question');
    expect(composer()).toBeDisabled();
    expect(onPendingChange).toHaveBeenLastCalledWith('event-two', true);
    await act(async () => { newAnswer.resolve(response({ ...turn, event_id: 'event-two', question: 'Is the ramp moving?', answer: 'Vehicles on the ramp are moving.' })); });
    expect(await screen.findByText('Vehicles on the ramp are moving.')).toBeInTheDocument();
    expect(onPendingChange).toHaveBeenLastCalledWith('event-two', false);
    await waitFor(() => expect(composer()).not.toBeDisabled());
  });

  it('shows queued then running progress and polls the same saved question without resubmitting', async () => {
    jest.useFakeTimers();
    let polls = 0;
    (global.fetch as jest.Mock).mockImplementation(async (url, options) => {
      if (options?.method === 'POST') return response({ ...turn, answer: undefined, status: 'queued' });
      if (String(url).endsWith('/request-1')) return response(++polls === 1
        ? { ...turn, answer: undefined, status: 'running' } : { ...turn, status: 'complete' });
      return response([]);
    });
    await load(); submit();
    await act(async () => {});
    expect(screen.getByRole('status')).toHaveTextContent('Queued — Cosmos will answer after its current review');
    expect(composer()).toBeDisabled();
    await act(async () => { await jest.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole('status')).toHaveTextContent('Cosmos is reviewing the selected clip');
    await act(async () => { await jest.advanceTimersByTimeAsync(1000); });
    expect(screen.getByText(turn.answer)).toBeInTheDocument();
    expect(posts()).toHaveLength(1);
    expect(composer()).toHaveValue('');
    expect(global.fetch).toHaveBeenCalledWith('/api/d1/reviews/event-one/questions/request-1', expect.objectContaining({ cache: 'no-store' }));
  });

  it('recovers queued history after reload using GET only and preserves saved failed questions', async () => {
    jest.useFakeTimers();
    const failed = { ...turn, id: 'older-failure', question: 'What can be seen?', answer: undefined, status: 'failed', error: { code: 'model_error', message: 'No usable answer returned.' } };
    (global.fetch as jest.Mock).mockImplementation(async url => response(String(url).endsWith('/request-1')
      ? { ...turn, status: 'complete' } : [failed, { ...turn, answer: undefined, status: 'queued' }]));
    render(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" />);
    await act(async () => {});
    expect(screen.getByRole('status')).toHaveTextContent('Queued');
    expect(screen.getByText('No usable answer returned.')).toBeInTheDocument();
    await act(async () => { await jest.advanceTimersByTimeAsync(1000); });
    expect(screen.getByText(turn.answer)).toBeInTheDocument();
    expect(posts()).toHaveLength(0);
  });

  it('recovers multiple queued questions on one clip without losing their identities', async () => {
    jest.useFakeTimers();
    const second = { ...turn, id: 'request-2', question: 'Which lane is moving?', answer: 'The far-left vehicles continue moving.' };
    (global.fetch as jest.Mock).mockImplementation(async url => response(String(url).endsWith('/request-1') ? { ...turn, status: 'complete' }
      : String(url).endsWith('/request-2') ? { ...second, status: 'complete' }
        : [{ ...turn, answer: undefined, status: 'queued' }, { ...second, answer: undefined, status: 'queued' }]));
    render(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" />);
    await act(async () => {});
    await act(async () => { await jest.advanceTimersByTimeAsync(2000); });
    expect(screen.getByText(turn.answer)).toBeInTheDocument();
    expect(screen.getByText(second.answer)).toBeInTheDocument();
    expect(posts()).toHaveLength(0);
  });

  it('retains the original request ID after polling fails and retries the existing job', async () => {
    jest.useFakeTimers();
    let submissions = 0;
    (global.fetch as jest.Mock).mockImplementation(async (_url, options) => {
      if (options?.method === 'POST') return response(++submissions === 1 ? { ...turn, answer: undefined, status: 'queued' } : { ...turn, status: 'complete' });
      if (String(_url).endsWith('/request-1')) throw new Error('Network unavailable.');
      return response([]);
    });
    await load(); submit(); await act(async () => {});
    await act(async () => { await jest.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole('alert')).toHaveTextContent('same queued question');
    expect(composer()).toHaveValue(turn.question);
    fireEvent.click(send()); await act(async () => {});
    expect(screen.getByText(turn.answer)).toBeInTheDocument();
    expect(posts()[0][1].body).toBe(posts()[1][1].body);
  });

  it('starts a new request only after an explicit retry of a terminal failed job', async () => {
    jest.useFakeTimers();
    const ids = jest.fn().mockReturnValueOnce('request-1').mockReturnValueOnce('request-2');
    Object.defineProperty(global.crypto, 'randomUUID', { configurable: true, value: ids });
    (global.fetch as jest.Mock).mockImplementation(async (url, options) => {
      if (options?.method === 'POST') {
        const id = JSON.parse(options.body).request_id;
        return response(id === 'request-1' ? { ...turn, answer: undefined, status: 'queued' } : { ...turn, id, status: 'complete' });
      }
      return response(String(url).endsWith('/request-1') ? { ...turn, answer: undefined, status: 'failed', error: { code: 'model_error', message: 'No usable answer returned.' } } : []);
    });
    await load(); submit(); await act(async () => {});
    await act(async () => { await jest.advanceTimersByTimeAsync(1000); });
    expect(screen.getByRole('alert')).toHaveTextContent('Retry to submit a new attempt');
    expect(composer()).not.toBeDisabled();
    fireEvent.click(send()); await act(async () => {});
    expect(screen.getByText(turn.answer)).toBeInTheDocument();
    expect(JSON.parse(posts()[0][1].body).request_id).toBe('request-1');
    expect(JSON.parse(posts()[1][1].body).request_id).toBe('request-2');
  });

  it('refuses a queued or completed envelope belonging to another request', async () => {
    (global.fetch as jest.Mock).mockImplementation(async (_url, options) => response(options?.method === 'POST'
      ? { ...turn, id: 'wrong-request', status: 'queued' } : []));
    await load(); submit();
    expect(await screen.findByRole('alert')).toHaveTextContent('answer did not match this clip');
    expect(screen.queryByText(turn.answer)).not.toBeInTheDocument();
    expect(composer()).toHaveValue(turn.question);
  });

  it('bounds a lost submit connection and keeps its request ID for a safe retry', async () => {
    jest.useFakeTimers();
    let submissions = 0;
    (global.fetch as jest.Mock).mockImplementation((_url, options) => {
      if (options?.method !== 'POST') return Promise.resolve(response([]));
      if (++submissions > 1) return Promise.resolve(response({ ...turn, status: 'complete' }));
      return new Promise((_resolve, reject) => options.signal.addEventListener('abort',
        () => reject(new DOMException('The request was aborted.', 'AbortError')), { once: true }));
    });
    await load(); submit(); await act(async () => {});
    await act(async () => { await jest.advanceTimersByTimeAsync(12000); });
    expect(screen.getByRole('alert')).toHaveTextContent('Connection interrupted');
    expect(composer()).toHaveValue(turn.question);
    expect(composer()).not.toBeDisabled();
    fireEvent.click(send()); await act(async () => {});
    expect(screen.getByText(turn.answer)).toBeInTheDocument();
    expect(posts()[0][1].body).toBe(posts()[1][1].body);
  });

  it('ignores a stale poll result after switching clips and releases the old busy state', async () => {
    jest.useFakeTimers();
    const oldPoll = deferred();
    const onPendingChange = jest.fn();
    (global.fetch as jest.Mock).mockImplementation((url, options) => String(url).endsWith('/request-1') ? oldPoll.promise
      : Promise.resolve(response(options?.method === 'POST' ? { ...turn, answer: undefined, status: 'queued' } : [])));
    const view = render(<EventQuestions eventId="event-one" clipReady clipTime="3:00 PM" onPendingChange={onPendingChange} />);
    await act(async () => {}); submit(); await act(async () => {});
    await act(async () => { await jest.advanceTimersByTimeAsync(1000); });
    view.rerender(<EventQuestions eventId="event-two" clipReady clipTime="3:01 PM" onPendingChange={onPendingChange} />);
    await act(async () => {});
    expect(onPendingChange).toHaveBeenLastCalledWith('event-one', false);
    await act(async () => { oldPoll.resolve(response({ ...turn, status: 'complete' })); });
    expect(screen.queryByText(turn.answer)).not.toBeInTheDocument();
    expect(composer()).toHaveValue('');
    expect(composer()).not.toBeDisabled();
  });
});
