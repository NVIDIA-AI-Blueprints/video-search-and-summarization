// SPDX-License-Identifier: MIT
/**
 * SharedViewComponent - read-only rendering of a published result set.
 *
 * An agent computes search results and publishes them to the share service;
 * this renders that payload at /view/<id>. Search hits are non-deterministic,
 * so the rows are transported by value rather than re-running the query.
 *
 * Read-only by construction: no search controls, no filters, no mutation. The
 * same VideoSearchList grid the live Search tab uses, so a shared view looks
 * like the product rather than a stripped-down export.
 *
 * Clip playback is gated on `vstApiUrl` because useVideoModal resolves clip
 * URLs against VST directly, which an off-LAN recipient cannot reach. Left
 * unset (the default for a public view), thumbnails still render and cards are
 * not clickable. Proxying clips through the share service is the follow-up.
 */
import React from 'react';

import { VideoSearchList } from './components/VideoSearchList';
import type { SearchData } from './types';

export interface SharedViewPayload {
  id: string;
  title: string;
  query?: string | null;
  created_at: string;
  expires_at: string;
  count: number;
  data: SearchData[];
}

export interface SharedViewComponentProps {
  /** Published view id, from the /view/<id> route. */
  viewId?: string;
  /**
   * Session slot to follow instead of a fixed id. When set, the component
   * polls the slot and re-renders whenever the agent publishes a new view.
   */
  sessionId?: string;
  /**
   * Origin serving /api/view/*. Empty means same-origin, which is the
   * deployed shape: the public ingress routes /api/view/* to vss-share and
   * everything else to this app.
   */
  apiBase?: string;
  /** Poll interval for `sessionId`, in ms. Ignored when following a fixed id. */
  pollIntervalMs?: number;
  isDark?: boolean;
  /** Enables clip playback. Only set when VST is reachable from the viewer. */
  vstApiUrl?: string;
}

const POLL_MIN_MS = 2000;

/**
 * The share service returns thumbnail paths relative to itself, which is
 * correct when one origin path-routes both the page and /api/view/*. Split
 * across two origins -- as on Brev, where every port gets its own secure link
 * -- the browser would resolve them against the page instead. Re-anchor them
 * to apiBase whenever one is configured.
 */
function anchorThumbnails(rows: SearchData[], base: string): SearchData[] {
  if (!base) return rows;
  return rows.map((row) =>
    row.screenshot_url?.startsWith('/') ? { ...row, screenshot_url: `${base}${row.screenshot_url}` } : row,
  );
}

function formatExpiry(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return '';
  return when.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

export const SharedViewComponent: React.FC<SharedViewComponentProps> = ({
  viewId,
  sessionId,
  apiBase = '',
  pollIntervalMs = 5000,
  isDark = true,
  vstApiUrl,
}) => {
  const [payload, setPayload] = React.useState<SharedViewPayload | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  // The share service returns the view id as the ETag, so an unchanged id
  // means unchanged content and the poll can be answered with a 304.
  const etagRef = React.useRef<string | null>(null);

  const base = apiBase.replace(/\/$/, '');
  const url = sessionId
    ? `${base}/api/view/session/${encodeURIComponent(sessionId)}`
    : viewId
      ? `${base}/api/view/${encodeURIComponent(viewId)}`
      : null;

  const load = React.useCallback(
    async (signal?: AbortSignal) => {
      if (!url) {
        setError('No view id or session id supplied.');
        setLoading(false);
        return;
      }
      try {
        const headers: Record<string, string> = {};
        if (etagRef.current) headers['If-None-Match'] = etagRef.current;

        const response = await fetch(url, { headers, signal });

        if (response.status === 304) {
          setLoading(false);
          return;
        }
        if (response.status === 404) {
          setPayload(null);
          setError('This view has expired or does not exist.');
          setLoading(false);
          return;
        }
        if (!response.ok) {
          setError(`Could not load view (${response.status}).`);
          setLoading(false);
          return;
        }

        const etag = response.headers.get('etag');
        if (etag) etagRef.current = etag;

        const body: SharedViewPayload = await response.json();
        setPayload({ ...body, data: anchorThumbnails(body.data ?? [], base) });
        setError(null);
      } catch (err) {
        if ((err as Error)?.name === 'AbortError') return;
        setError('Could not reach the view service.');
      } finally {
        setLoading(false);
      }
    },
    [url, base],
  );

  React.useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  // Only a session slot changes over time; a fixed id is immutable.
  React.useEffect(() => {
    if (!sessionId) return undefined;
    const interval = Math.max(POLL_MIN_MS, pollIntervalMs);
    const timer = setInterval(() => void load(), interval);
    return () => clearInterval(timer);
  }, [sessionId, pollIntervalMs, load]);

  const noop = React.useCallback(() => undefined, []);
  const playbackEnabled = Boolean(vstApiUrl);

  const surface = isDark ? 'bg-[#111418] text-gray-100' : 'bg-white text-gray-900';
  const muted = isDark ? 'text-gray-400' : 'text-gray-600';

  return (
    <div className={`flex flex-col h-full ${surface}`} data-testid="shared-view">
      <header className={`px-6 py-4 border-b ${isDark ? 'border-gray-800' : 'border-gray-200'}`}>
        <h1 className="text-xl font-semibold">{payload?.title ?? 'Shared results'}</h1>
        <p className={`text-sm mt-1 ${muted}`}>
          {payload
            ? `${payload.count} result${payload.count === 1 ? '' : 's'}` +
              (payload.expires_at ? ` · expires ${formatExpiry(payload.expires_at)}` : '')
            : 'Loading…'}
        </p>
        {!playbackEnabled && payload ? (
          <p className={`text-xs mt-1 ${muted}`}>
            Read-only view. Clip playback is unavailable outside the deployment network.
          </p>
        ) : null}
      </header>

      <div className="flex-1 overflow-auto">
        <VideoSearchList
          data={payload?.data ?? []}
          loading={loading}
          error={error}
          isDark={isDark}
          onRefresh={() => void load()}
          onPlayVideo={noop}
          showObjectsBbox={false}
        />
      </div>
    </div>
  );
};
