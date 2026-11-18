// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import React, { useEffect, useRef, useState } from 'react';
import styles from './SourcePreview.module.css';

type Props = { streamId: string; revision: number; paused: boolean };
type Frame = { key: string; url: string | null; status: 'connecting' | 'waiting' };

export default function SourcePreview({ streamId, revision, paused }: Props) {
  const key = JSON.stringify([streamId, revision]);
  const [hidden, setHidden] = useState(() => typeof document !== 'undefined' && document.hidden);
  const [frame, setFrame] = useState<Frame>({ key, url: null, status: 'connecting' });
  const imageError = useRef<((url: string | null) => void) | null>(null);
  const stopped = paused || hidden;

  useEffect(() => {
    const visibilityChanged = () => setHidden(document.hidden);
    document.addEventListener('visibilitychange', visibilityChanged);
    return () => document.removeEventListener('visibilitychange', visibilityChanged);
  }, []);

  useEffect(() => {
    let disposed = false;
    let objectUrl: string | null = null;
    let controller: AbortController | null = null;
    let nextPoll: ReturnType<typeof setTimeout> | undefined;
    let timeout: ReturnType<typeof setTimeout> | undefined;
    const releaseFrame = () => {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      objectUrl = null;
    };
    const waitForFreshVideo = () => {
      releaseFrame();
      if (!disposed) setFrame({ key, url: null, status: 'waiting' });
    };
    imageError.current = url => {
      if (url === objectUrl && objectUrl) waitForFreshVideo();
    };
    setFrame({ key, url: null, status: 'connecting' });

    async function poll() {
      const request = new AbortController();
      controller = request;
      timeout = setTimeout(() => {
        request.abort();
        waitForFreshVideo();
      }, 3000);
      try {
        const response = await fetch(`/api/proxy/d1/preview/${encodeURIComponent(streamId)}?revision=${encodeURIComponent(String(revision))}`, {
          cache: 'no-store', headers: { Accept: 'image/jpeg' }, signal: request.signal,
        });
        if (!response.ok) throw new Error('Fresh video is not available.');
        const blob = await response.blob();
        if (disposed || request.signal.aborted) return;
        if (blob.type.split(';')[0] !== 'image/jpeg' || !blob.size) throw new Error('No video frame was returned.');
        const nextUrl = URL.createObjectURL(blob);
        releaseFrame();
        objectUrl = nextUrl;
        setFrame({ key, url: nextUrl, status: 'connecting' });
      } catch {
        if (!disposed) waitForFreshVideo();
      } finally {
        clearTimeout(timeout);
        if (!disposed) nextPoll = setTimeout(() => { void poll(); }, 200);
      }
    }

    if (!stopped && streamId && !document.hidden) void poll();
    return () => {
      disposed = true;
      clearTimeout(nextPoll);
      clearTimeout(timeout);
      controller?.abort();
      releaseFrame();
      imageError.current = null;
    };
  }, [streamId, revision, key, stopped]);

  const visibleFrame = !stopped && frame.key === key ? frame.url : null;
  const message = stopped ? 'Preview paused' : frame.key === key && frame.status === 'waiting'
    ? 'Waiting for fresh video…' : 'Connecting to monitored video…';
  return <section className={styles.preview} aria-label="Current monitored video">
    {visibleFrame ? <img className={styles.frame} src={visibleFrame} alt="Current monitored video" width={960} height={540}
      onError={event => imageError.current?.(event.currentTarget.getAttribute('src'))} />
      : <p className={styles.status} role="status">{message}</p>}
  </section>;
}
