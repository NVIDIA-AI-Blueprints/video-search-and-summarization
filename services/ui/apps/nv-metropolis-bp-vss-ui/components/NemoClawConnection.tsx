// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React, { useCallback, useEffect, useId, useMemo, useRef, useState } from 'react';

const STORAGE_KEY = 'vss-nemoclaw-gateway-token';
export const GATEWAY_TOKEN_HEADER = 'X-VSS-Gateway-Token';

const tokenFromFragment = (): string | null => {
  if (!window.location.hash.startsWith('#token=')) return null;
  return new URLSearchParams(window.location.hash.slice(1)).get('token') ?? '';
};

const clearTokenFragment = (): void => {
  if (tokenFromFragment() !== null) {
    window.history.replaceState(
      window.history.state,
      '',
      `${window.location.pathname}${window.location.search}`,
    );
  }
};

type ConnectionState = 'checking' | 'connected' | 'token_required' | 'authentication_failed' | 'unreachable';

export interface NemoClawConnection {
  state: ConnectionState;
  hasConnected: boolean;
  token: string;
  connect: (token: string) => Promise<void>;
  retry: () => Promise<void>;
  changeToken: () => void;
}

export function useNemoClawConnection(enabled: boolean): NemoClawConnection {
  const [state, setState] = useState<ConnectionState>(enabled ? 'checking' : 'connected');
  const [hasConnected, setHasConnected] = useState(false);
  const [token, setToken] = useState('');
  const requestNumber = useRef(0);

  const check = useCallback(async (value: string) => {
    const request = ++requestNumber.current;
    setState('checking');
    try {
      const response = await fetch('/api/agent/connection', {
        headers: value ? { [GATEWAY_TOKEN_HEADER]: value } : {},
        cache: 'no-store',
      });
      const body = await response.json();
      if (request !== requestNumber.current) return;
      if (!response.ok) {
        setState('unreachable');
      } else if (['connected', 'token_required', 'authentication_failed', 'unreachable'].includes(body.state)) {
        setState(body.state);
        if (body.state === 'connected') setHasConnected(true);
      } else {
        setState('unreachable');
      }
    } catch {
      if (request === requestNumber.current) setState('unreachable');
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    const applyFragment = (): boolean => {
      const fragmentToken = tokenFromFragment();
      if (fragmentToken === null) return false;
      const next = fragmentToken.trim();
      if (!next || next.length > 4_096 || /[\r\n]/u.test(next)) return false;
      try {
        sessionStorage.setItem(STORAGE_KEY, next);
      } catch { /* Private browsing may deny storage. */ }
      setToken(next);
      void check(next);
      return true;
    };
    if (!applyFragment()) {
      let saved = '';
      try { saved = sessionStorage.getItem(STORAGE_KEY) ?? ''; } catch { /* Private browsing may deny storage. */ }
      setToken(saved);
      void check(saved);
    }
    window.addEventListener('hashchange', applyFragment);
    return () => {
      window.removeEventListener('hashchange', applyFragment);
      requestNumber.current += 1;
    };
  }, [enabled, check]);

  const connect = useCallback(async (value: string) => {
    const next = value.trim();
    if (!next) return;
    clearTokenFragment();
    try { sessionStorage.setItem(STORAGE_KEY, next); } catch { /* This tab still keeps the token in memory. */ }
    setToken(next);
    await check(next);
  }, [check]);

  const retry = useCallback(() => check(token), [check, token]);
  const changeToken = useCallback(() => {
    requestNumber.current += 1;
    clearTokenFragment();
    try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* Storage is optional. */ }
    setToken('');
    setHasConnected(false);
    setState('token_required');
  }, []);

  return useMemo(() => ({ state, hasConnected, token, connect, retry, changeToken }), [state, hasConnected, token, connect, retry, changeToken]);
}

export function NemoClawConnectionPanel({ connection }: { connection: NemoClawConnection }) {
  const [draft, setDraft] = useState('');
  const tokenFieldId = useId();
  const { state, token, connect, retry, changeToken } = connection;

  return (
    <div className="flex h-full min-h-0 items-center justify-center overflow-y-auto bg-white p-5 text-gray-900 dark:bg-black dark:text-gray-100">
      <div className="w-full max-w-md rounded-lg border border-gray-300 p-5 dark:border-neutral-700">
        <h2 className="text-lg font-semibold">Connect NemoClaw chat</h2>
        {state === 'checking' ? <p role="status" className="mt-3 text-sm">Checking the NemoClaw gateway…</p> : null}
        {state === 'unreachable' ? (
          <p role="status" className="mt-3 text-sm">The NemoClaw gateway is not reachable yet. Start or repair NemoClaw, then retry.</p>
        ) : null}
        {state === 'authentication_failed' ? (
          <p role="alert" className="mt-3 text-sm">The gateway rejected the connection. Check the token and gateway access settings.</p>
        ) : null}
        {state === 'token_required' ? (
          <p className="mt-3 text-sm">On the deployment host, run <code>nemoclaw &lt;sandbox&gt; gateway-token --quiet</code> and enter the token here, or open a link ending in <code>#token=&lt;URL-encoded token&gt;</code>.</p>
        ) : null}
        {state !== 'checking' ? (
          <form className="mt-4 flex flex-col gap-3" onSubmit={(event) => { event.preventDefault(); void connect(draft); setDraft(''); }}>
            <label className="text-sm font-medium" htmlFor={tokenFieldId}>Gateway token</label>
            <input
              id={tokenFieldId}
              type="password"
              autoComplete="off"
              maxLength={4096}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              className="w-full rounded border border-gray-400 bg-white px-3 py-2 text-gray-900 dark:bg-neutral-900 dark:text-gray-100"
            />
            <div className="flex flex-wrap gap-2">
              <button type="submit" disabled={!draft.trim()} className="rounded bg-green-700 px-3 py-2 text-sm text-white disabled:opacity-50">Connect</button>
              {(token || state === 'unreachable') ? <button type="button" onClick={() => void retry()} className="rounded border px-3 py-2 text-sm">Retry gateway</button> : null}
              {token ? <button type="button" onClick={changeToken} className="rounded border px-3 py-2 text-sm">Forget token</button> : null}
            </div>
          </form>
        ) : null}
        <p className="mt-4 text-xs text-gray-500 dark:text-gray-400">The token is sent only to this UI's agent API. A token in a shared URL stays in the address bar until you change it; anyone with that link can use the token.</p>
      </div>
    </div>
  );
}

export function NemoClawConnectionBadge({ connection }: { connection: NemoClawConnection }) {
  const status = {
    checking: 'Checking NemoClaw gateway…',
    connected: 'NemoClaw connected',
    token_required: 'NemoClaw token required',
    authentication_failed: 'NemoClaw token rejected',
    unreachable: 'NemoClaw gateway unavailable',
  }[connection.state];
  return (
    <div className="flex shrink-0 items-center justify-between gap-2 border-b border-gray-200 bg-white px-3 py-1 text-xs text-gray-600 dark:border-neutral-700 dark:bg-black dark:text-gray-300">
      <span role="status">{status}</span>
      <div className="flex gap-3">
        <button type="button" onClick={() => void connection.retry()} className="underline">Check connection</button>
        <button type="button" onClick={connection.changeToken} className="underline">Change token</button>
      </div>
    </div>
  );
}
