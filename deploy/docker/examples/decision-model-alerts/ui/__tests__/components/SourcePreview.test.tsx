// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import SourcePreview from '../../components/SourcePreview';

const jpeg = () => ({ ok: true, blob: async () => new Blob(['fresh frame'], { type: 'image/jpeg' }) });
const flush = () => act(async () => { await Promise.resolve(); });
const advance = (ms: number) => act(async () => { jest.advanceTimersByTime(ms); });
const shownFrame = () => screen.queryByRole('img', { name: 'Current monitored video' });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(accept => { resolve = accept; });
  return { promise, resolve };
}

describe('Current monitored video preview', () => {
  let createObjectURL: jest.Mock;
  let revokeObjectURL: jest.Mock;
  let hidden: boolean;
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  const originalFetch = global.fetch;
  const originalHidden = Object.getOwnPropertyDescriptor(document, 'hidden');

  beforeEach(() => {
    jest.useFakeTimers();
    hidden = false;
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
    let serial = 0;
    createObjectURL = jest.fn(() => `blob:frame-${++serial}`);
    revokeObjectURL = jest.fn();
    URL.createObjectURL = createObjectURL;
    URL.revokeObjectURL = revokeObjectURL;
    global.fetch = jest.fn(async () => jpeg()) as jest.Mock;
  });

  afterEach(() => {
    cleanup();
    jest.useRealTimers();
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
    global.fetch = originalFetch;
    if (originalHidden) Object.defineProperty(document, 'hidden', originalHidden);
    else Reflect.deleteProperty(document, 'hidden');
  });

  it('uses the fixed same-origin stream route and waits between completed requests without overlap', async () => {
    const pending = deferred<ReturnType<typeof jpeg>>();
    (global.fetch as jest.Mock).mockReturnValueOnce(pending.promise);
    render(<SourcePreview streamId="traffic/example" revision={7} paused={false} />);
    expect(screen.getByRole('status')).toHaveTextContent('Connecting to monitored video…');
    expect(global.fetch).toHaveBeenCalledWith('/api/proxy/d1/preview/traffic%2Fexample?revision=7', expect.objectContaining({
      cache: 'no-store', headers: { Accept: 'image/jpeg' }, signal: expect.any(AbortSignal),
    }));
    await advance(1000);
    expect(global.fetch).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve(jpeg()));
    expect(shownFrame()).toHaveAttribute('src', 'blob:frame-1');
    await advance(199);
    expect(global.fetch).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(global.fetch).toHaveBeenCalledTimes(2);
    expect(shownFrame()).toHaveAttribute('src', 'blob:frame-2');
  });

  it('releases replaced frames and aborts and releases the current frame on unmount', async () => {
    const { unmount } = render(<SourcePreview streamId="flowing" revision={1} paused={false} />);
    await flush();
    await advance(200);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:frame-1');
    const secondSignal = (global.fetch as jest.Mock).mock.calls[1][1].signal;
    unmount();
    expect(secondSignal.aborted).toBe(true);
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:frame-2');
    await advance(1000);
    expect(global.fetch).toHaveBeenCalledTimes(2);
  });

  it.each([409, 503])('clears the previous image when fresh video returns HTTP %s and recovers on a new frame', async status => {
    render(<SourcePreview streamId="flowing" revision={1} paused={false} />);
    await flush();
    (global.fetch as jest.Mock).mockResolvedValueOnce({ ok: false, status });
    await advance(200);
    expect(shownFrame()).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Waiting for fresh video…');
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:frame-1');
    await advance(200);
    expect(shownFrame()).toHaveAttribute('src', 'blob:frame-2');
  });

  it('clears a stalled frame after three seconds and ignores a late timed-out response', async () => {
    render(<SourcePreview streamId="flowing" revision={1} paused={false} />);
    await flush();
    const pending = deferred<ReturnType<typeof jpeg>>();
    (global.fetch as jest.Mock).mockReturnValueOnce(pending.promise);
    await advance(200);
    const signal = (global.fetch as jest.Mock).mock.calls[1][1].signal;
    await advance(2999);
    expect(signal.aborted).toBe(false);
    expect(shownFrame()).toBeInTheDocument();
    await advance(1);
    expect(signal.aborted).toBe(true);
    expect(shownFrame()).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Waiting for fresh video…');
    expect(global.fetch).toHaveBeenCalledTimes(2);
    await act(async () => pending.resolve(jpeg()));
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(shownFrame()).not.toBeInTheDocument();
  });

  it.each([
    ['a new stream', 'congested', 1],
    ['a new semantic revision', 'flowing', 2],
  ])('clears evidence and ignores old responses after selecting %s', async (_name, streamId, revision) => {
    const { rerender } = render(<SourcePreview streamId="flowing" revision={1} paused={false} />);
    await flush();
    const oldPending = deferred<ReturnType<typeof jpeg>>();
    const newPending = deferred<ReturnType<typeof jpeg>>();
    (global.fetch as jest.Mock).mockReturnValueOnce(oldPending.promise).mockReturnValueOnce(newPending.promise);
    await advance(200);
    const oldSignal = (global.fetch as jest.Mock).mock.calls[1][1].signal;
    rerender(<SourcePreview streamId={String(streamId)} revision={Number(revision)} paused={false} />);
    expect(oldSignal.aborted).toBe(true);
    expect(shownFrame()).not.toBeInTheDocument();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:frame-1');
    expect(screen.getByRole('status')).toHaveTextContent('Connecting to monitored video…');
    expect(global.fetch).toHaveBeenLastCalledWith(`/api/proxy/d1/preview/${streamId}?revision=${revision}`, expect.anything());
    await act(async () => newPending.resolve(jpeg()));
    expect(shownFrame()).toHaveAttribute('src', 'blob:frame-2');
    await act(async () => oldPending.resolve(jpeg()));
    expect(shownFrame()).toHaveAttribute('src', 'blob:frame-2');
    expect(createObjectURL).toHaveBeenCalledTimes(2);
  });

  it('clears and suspends preview while paused or hidden, then fetches a new frame when resumed', async () => {
    const { rerender } = render(<SourcePreview streamId="flowing" revision={1} paused={false} />);
    await flush();
    rerender(<SourcePreview streamId="flowing" revision={1} paused />);
    expect(shownFrame()).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Preview paused');
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:frame-1');
    await advance(1000);
    expect(global.fetch).toHaveBeenCalledTimes(1);
    rerender(<SourcePreview streamId="flowing" revision={1} paused={false} />);
    await flush();
    expect(shownFrame()).toHaveAttribute('src', 'blob:frame-2');
    hidden = true;
    fireEvent(document, new Event('visibilitychange'));
    expect(shownFrame()).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Preview paused');
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:frame-2');
    await advance(1000);
    expect(global.fetch).toHaveBeenCalledTimes(2);
    hidden = false;
    fireEvent(document, new Event('visibilitychange'));
    await flush();
    expect(shownFrame()).toHaveAttribute('src', 'blob:frame-3');
  });

  it('clears network failures, invalid image bytes, and browser image errors instead of freezing a frame', async () => {
    render(<SourcePreview streamId="flowing" revision={1} paused={false} />);
    await flush();
    (global.fetch as jest.Mock).mockRejectedValueOnce(new Error('network disconnected'));
    await advance(200);
    expect(shownFrame()).not.toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Waiting for fresh video…');
    (global.fetch as jest.Mock).mockResolvedValueOnce({ ok: true, blob: async () => new Blob(['error'], { type: 'application/json' }) });
    await advance(200);
    expect(shownFrame()).not.toBeInTheDocument();
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    await advance(200);
    fireEvent.error(shownFrame()!);
    expect(shownFrame()).not.toBeInTheDocument();
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:frame-2');
    expect(screen.getByRole('status')).toHaveTextContent('Waiting for fresh video…');
  });
});
