// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
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
 * The server-only VSS_PROXY_BASE_URL selects the internal deployment target.
 * Compose points it at haproxy; Helm points it directly at vss-vios-ingress.
 * The Compose service remains the default for backwards compatibility.
 *
 * SCOPE: deliberately narrow. This route is reachable from the public internet
 * and reaches the ingress by its internal address, which haproxy leaves
 * unauthenticated -- so an unrestricted catch-all would let anyone relay
 * arbitrary methods and paths to internal APIs (Elasticsearch writes, for one)
 * with the public auth stripped off. It therefore forwards only safe methods to
 * an allowlisted path prefix: the search-hit media it exists to serve.
 */
import type { NextApiRequest, NextApiResponse } from 'next';

export const getVssProxyBaseUrl = (
  environment: NodeJS.ProcessEnv = process.env,
): string => {
  const raw =
    environment.VSS_PROXY_BASE_URL?.trim() ||
    `http://vss-haproxy-ingress:${environment.HAPROXY_PORT || '7777'}`;
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new Error('VSS_PROXY_BASE_URL must be an absolute URL');
  }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('VSS_PROXY_BASE_URL must be an http(s) URL without embedded credentials');
  }
  if (url.search || url.hash) {
    throw new Error('VSS_PROXY_BASE_URL must not contain a query or fragment');
  }
  return url.toString().replace(/\/$/, '');
};

export const config = {
  api: {
    bodyParser: false, // stream bodies through untouched (uploads included)
    responseLimit: false,
  },
};

/** Only these reach the ingress; everything else is refused. */
const ALLOWED_METHODS = new Set(['GET', 'HEAD']);

/**
 * Path prefixes this proxy will relay. The structured agent transport rewrites
 * `*_url` fields on structured artifacts to this route. Widen only with a
 * matching reason; every addition is publicly reachable.
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

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (!ALLOWED_METHODS.has((req.method || '').toUpperCase())) {
    res.status(405).json({ error: 'method not allowed' });
    return;
  }

  const segments = Array.isArray(req.query.path) ? req.query.path : [req.query.path ?? ''];
  const search = req.url?.includes('?') ? req.url.slice(req.url.indexOf('?')) : '';
  const path = `/${segments.join('/')}`;

  // Resolve before checking so `..` cannot walk out of an allowed prefix.
  const normalised = new URL(path, 'https://placeholder.invalid').pathname;
  if (!ALLOWED_PREFIXES.some((prefix) => normalised.startsWith(prefix))) {
    res.status(403).json({ error: 'path not permitted by proxy allowlist' });
    return;
  }

  let target: string;
  try {
    target = `${getVssProxyBaseUrl()}${normalised}${search}`;
  } catch (err) {
    res.status(500).json({
      error: err instanceof Error ? err.message : 'invalid VSS proxy configuration',
    });
    return;
  }

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
