// SPDX-License-Identifier: MIT
/**
 * Server-side proxy to the agent backend.
 *
 * The browser must not talk to the adapter directly: it sits on a host-private
 * port, and keeping it server-side means the adapter's address (and any token)
 * never reaches the client. The browser calls this same-origin route instead,
 * and the SSE stream is piped straight through untouched — the parsing all
 * happens in the chat package.
 *
 * Targets are configured with server-only env vars, so they are not inlined
 * into the client bundle:
 *
 *   VSS_CHAT_BACKEND_MAIN      default http://127.0.0.1:9097/chat/stream
 *   VSS_CHAT_BACKEND_SIDEBAR   default http://127.0.0.1:9098/chat/stream
 */
import type { NextApiRequest, NextApiResponse } from 'next';

const BACKENDS: Record<string, string> = {
  main: process.env.VSS_CHAT_BACKEND_MAIN || 'http://127.0.0.1:9097/chat/stream',
  sidebar: process.env.VSS_CHAT_BACKEND_SIDEBAR || 'http://127.0.0.1:9098/chat/stream',
};

export const config = { api: { bodyParser: { sizeLimit: '5mb' } } };

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== 'POST') {
    res.status(405).json({ error: 'method not allowed' });
    return;
  }

  const surface = String(req.query.surface ?? 'main');
  const target = BACKENDS[surface];
  if (!target) {
    res.status(400).json({ error: `unknown surface: ${surface}` });
    return;
  }

  const controller = new AbortController();
  // If the user navigates away or hits Stop, drop the upstream turn too.
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
      // Next buffers by default; SSE is useless unless each frame is flushed.
      (res as unknown as { flush?: () => void }).flush?.();
    }
  } catch {
    // Client hung up or upstream died; nothing useful to add to the stream.
  } finally {
    reader.releaseLock();
    res.end();
  }
}
