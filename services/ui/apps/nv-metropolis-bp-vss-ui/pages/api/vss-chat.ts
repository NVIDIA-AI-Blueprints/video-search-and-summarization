// SPDX-License-Identifier: MIT
/**
 * Server-side proxy from the VSS chat surfaces to the agent backend.
 *
 * Needed because the replacement ChatPanel fetches from the browser, while the
 * configured backends (the agent adapter) sit on host-private ports that a
 * browser cannot reach. Proxying server-side also keeps the adapter's address
 * out of the client bundle.
 *
 * The SSE stream is piped through untouched; all parsing happens in
 * @nv-metropolis-bp-vss-ui/chat.
 *
 * Targets resolve from server-only vars first, then fall back to the existing
 * NEXT_PUBLIC_* chat completion URLs so an existing deployment needs no new
 * configuration.
 */
import type { NextApiRequest, NextApiResponse } from 'next';

const backendFor = (surface: string): string | undefined => {
  if (surface === 'sidebar') {
    return (
      process.env.VSS_CHAT_BACKEND_SIDEBAR ||
      process.env.NEXT_PUBLIC_SIDEBAR_CHAT_HTTP_CHAT_COMPLETION_URL ||
      process.env.NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL
    );
  }
  if (surface === 'main') {
    return process.env.VSS_CHAT_BACKEND_MAIN || process.env.NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL;
  }
  return undefined;
};

export const config = { api: { bodyParser: { sizeLimit: '5mb' } } };

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  // GET returns the most recent search result from the adapter, so the Search
  // tab can render hits that never passed through the model's reply text.
  if (req.method === 'GET') {
    const base = (backendFor(String(req.query.surface ?? 'sidebar')) || '').replace(
      /\/chat\/stream$/,
      '',
    );
    // Forward the conversation so the adapter serves only that conversation's
    // result rather than whatever ran last, process-wide.
    const conversation = String(req.query.conversation ?? '');
    const qs = conversation ? `?conversation=${encodeURIComponent(conversation)}` : '';
    try {
      const upstream = await fetch(`${base}/v1/search/last${qs}`);
      res.status(upstream.status).json(await upstream.json());
    } catch (err) {
      res.status(502).json({
        error: 'could not read last search result',
        detail: err instanceof Error ? err.message : String(err),
      });
    }
    return;
  }

  if (req.method !== 'POST') {
    res.status(405).json({ error: 'method not allowed' });
    return;
  }

  const surface = String(req.query.surface ?? 'main');
  const target = backendFor(surface);
  if (!target) {
    res.status(400).json({ error: `no backend configured for surface: ${surface}` });
    return;
  }

  const controller = new AbortController();
  // Navigating away or pressing Stop should drop the upstream turn too.
  req.on('close', () => controller.abort());

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: 'POST',
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        'Conversation-Id': String(req.headers['conversation-id'] ?? ''),
      },
      body: JSON.stringify(req.body ?? {}),
    });
  } catch (err) {
    res.status(502).json({
      error: `agent backend unreachable at ${target}`,
      detail: err instanceof Error ? err.message : String(err),
    });
    return;
  }

  if (!upstream.ok || !upstream.body) {
    res.status(502).json({ error: `agent backend returned HTTP ${upstream.status}` });
    return;
  }

  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache, no-transform',
    Connection: 'close',
    'X-Accel-Buffering': 'no',
  });

  const reader = upstream.body.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(Buffer.from(value));
      // Next buffers by default; without flushing, SSE arrives all at once.
      (res as unknown as { flush?: () => void }).flush?.();
    }
  } catch {
    // Client hung up or upstream died mid-turn.
  } finally {
    reader.releaseLock();
    res.end();
  }
}
