// SPDX-License-Identifier: MIT
/**
 * Same-origin proxy to the VSS ingress, for running the UI outside its normal
 * deployment origin.
 *
 * In the deployed container the UI and every VSS API share one origin behind
 * haproxy, so browser fetches work directly. Served from anywhere else — a dev
 * server, a different tunnel — those same absolute URLs become cross-origin
 * *and* hit haproxy's basic auth, which the browser has no credentials for, and
 * every tab fails with "Failed to fetch".
 *
 * Forwarding through the server fixes both: the request is same-origin from the
 * browser's point of view, and it reaches the ingress by its internal address,
 * which haproxy leaves unauthenticated (auth only fires for traffic arriving
 * via Cloudflare).
 *
 * Set VSS_INGRESS_ORIGIN to point at the ingress; defaults to the local one.
 *
 * SCOPE: deliberately narrow. This route is reachable from the public internet
 * and reaches the ingress by its internal address, which haproxy leaves
 * unauthenticated -- so an unrestricted catch-all would let anyone relay
 * arbitrary methods and paths to internal APIs (Elasticsearch writes, for one)
 * with the public auth stripped off. It therefore forwards only safe methods to
 * an allowlisted path prefix: the search-hit media it exists to serve.
 */
import type { NextApiRequest, NextApiResponse } from 'next';

const INGRESS = (process.env.VSS_INGRESS_ORIGIN || 'http://127.0.0.1:7777').replace(/\/$/, '');

export const config = {
  api: {
    bodyParser: false, // stream bodies through untouched (uploads included)
    responseLimit: false,
  },
};

/** Only these reach the ingress; everything else is refused. */
const ALLOWED_METHODS = new Set(['GET', 'HEAD']);

/**
 * Path prefixes this proxy will relay. The sole caller is `proxyMediaUrl` in
 * Home.tsx, rewriting `*_url` fields on search hits -- VST replay media. Widen
 * only with a matching reason; every addition is publicly reachable.
 */
const ALLOWED_PREFIXES = (process.env.VSS_PROXY_ALLOWED_PREFIXES || '/vst/')
  .split(',')
  .map((p) => p.trim())
  .filter(Boolean);

const HOP_BY_HOP = new Set([
  'connection',
  'keep-alive',
  'transfer-encoding',
  'upgrade',
  'host',
  'content-length',
]);

/**
 * Headers that must not be forwarded to the ingress.
 *
 * This is the whole point of the proxy: haproxy challenges any request carrying
 * CF-Connecting-IP, since that marks traffic arriving via Cloudflare. When the
 * UI is reached through a tunnel the browser's request has those headers, and
 * blindly forwarding them makes this server-to-server call look like public
 * traffic and get a 401 -- which surfaces in the browser as a basic-auth prompt
 * and "Failed to fetch streams: 401".
 *
 * Forwarded/X-Forwarded-* go too, for the same reason.
 */
const isProxyOnlyHeader = (name: string) =>
  name.startsWith('cf-') || name.startsWith('x-forwarded-') || name === 'forwarded';

async function readBody(req: NextApiRequest): Promise<Buffer | undefined> {
  if (req.method === 'GET' || req.method === 'HEAD') return undefined;
  const chunks: Buffer[] = [];
  for await (const chunk of req) chunks.push(Buffer.from(chunk));
  return chunks.length ? Buffer.concat(chunks) : undefined;
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ALLOWED_METHODS.has((req.method || '').toUpperCase())) {
    res.status(405).json({ error: 'method not allowed' });
    return;
  }

  const segments = Array.isArray(req.query.path) ? req.query.path : [req.query.path ?? ''];
  const search = req.url?.includes('?') ? req.url.slice(req.url.indexOf('?')) : '';
  const path = `/${segments.join('/')}`;

  // Resolve before checking so `..` cannot walk out of an allowed prefix.
  const normalised = new URL(path, 'http://placeholder').pathname;
  if (!ALLOWED_PREFIXES.some((prefix) => normalised.startsWith(prefix))) {
    res.status(403).json({ error: 'path not permitted by proxy allowlist' });
    return;
  }

  const target = `${INGRESS}${normalised}${search}`;

  const headers: Record<string, string> = {};
  for (const [k, v] of Object.entries(req.headers)) {
    const key = k.toLowerCase();
    if (!HOP_BY_HOP.has(key) && !isProxyOnlyHeader(key) && typeof v === 'string') {
      headers[k] = v;
    }
  }

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: req.method,
      headers,
      body: await readBody(req),
      redirect: 'manual',
    });
  } catch (err) {
    res.status(502).json({
      error: `VSS ingress unreachable at ${target}`,
      detail: err instanceof Error ? err.message : String(err),
    });
    return;
  }

  upstream.headers.forEach((value, key) => {
    if (!HOP_BY_HOP.has(key.toLowerCase())) res.setHeader(key, value);
  });
  res.status(upstream.status);

  if (!upstream.body) {
    res.end();
    return;
  }

  const reader = upstream.body.getReader();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(Buffer.from(value));
    }
  } catch {
    // Client disconnected or upstream failed mid-response.
  } finally {
    reader.releaseLock();
    res.end();
  }
}
