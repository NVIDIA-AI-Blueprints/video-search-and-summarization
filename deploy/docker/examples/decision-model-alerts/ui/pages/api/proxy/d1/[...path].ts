// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import type { NextApiRequest, NextApiResponse } from 'next';

// Fixed upstreams and operations: the browser cannot choose a URL or an ES query.
const ingest = () => process.env.D1_INGEST_URL || 'http://127.0.0.1:8020';
const analytics = () => process.env.D1_ANALYTICS_URL || 'http://127.0.0.1:8021';
const elastic = () => process.env.D1_ELASTICSEARCH_URL || 'http://127.0.0.1:9200';
async function upstream(url: string, method = 'GET', body?: unknown) {
  const result = await fetch(url, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: AbortSignal.timeout(10000), redirect: 'error',
  });
  const data = await result.json();
  if (!result.ok) throw new Error(`Service returned ${result.status}: ${JSON.stringify(data.detail || data.error || data).slice(0, 600)}`);
  return data;
}
export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  res.setHeader('Cache-Control', 'no-store');
  if (process.env.D1_AGENT_ENABLED !== 'true') return res.status(404).json({ error: 'D1 integration is disabled' });
  const parts = req.query.path;
  if (!Array.isArray(parts)) return res.status(404).end();
  if (req.method === 'GET' && parts.length === 2 && parts[0] === 'preview' && /^[a-zA-Z0-9_-]{1,64}$/.test(parts[1])) {
    const revision = req.query.revision;
    if (typeof revision !== 'string' || !/^[1-9]\d{0,9}$/.test(revision)) return res.status(400).json({ error: 'Stream revision required' });
    try {
      const frame = await fetch(`${ingest()}/v1/streams/${parts[1]}/preview?revision=${revision}`, {
        signal: AbortSignal.timeout(2500), redirect: 'error', cache: 'no-store',
      });
      if (!frame.ok) return res.status([404, 409, 503].includes(frame.status) ? frame.status : 502).json({ error: 'Waiting for fresh video' });
      if (frame.headers.get('content-type')?.split(';')[0] !== 'image/jpeg') return res.status(502).json({ error: 'Preview unavailable' });
      const bytes = Buffer.from(await frame.arrayBuffer());
      if (bytes.length > 2 * 1024 * 1024) return res.status(502).json({ error: 'Preview unavailable' });
      for (const key of ['x-frame-sequence', 'x-frame-captured-at', 'x-stream-revision']) {
        const value = frame.headers.get(key); if (value) res.setHeader(key, value);
      }
      res.setHeader('Content-Type', 'image/jpeg');
      return res.status(200).send(bytes);
    } catch { return res.status(502).json({ error: 'Waiting for fresh video' }); }
  }
  if (req.method === 'GET' && parts.length === 2 && parts[0] === 'clips' && /^[a-zA-Z0-9_-]{1,100}$/.test(parts[1])) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 90000);
    res.on('close', () => { if (!res.writableFinished) controller.abort(); });
    try {
      const headers: Record<string, string> = {};
      if (req.headers.range && /^bytes=\d*-\d*$/.test(req.headers.range)) headers.Range = req.headers.range;
      const media = await fetch(`${analytics()}/v1/reviews/${parts[1]}/clip`, {
        headers, signal: controller.signal, redirect: 'error',
      });
      if (!media.ok || !media.body) return res.status(media.status).json({ error: media.status === 409 ? 'Clip is being prepared' : 'Clip unavailable' });
      res.status(media.status);
      for (const key of ['content-type', 'content-length', 'content-range', 'accept-ranges']) {
        const value = media.headers.get(key); if (value) res.setHeader(key, value);
      }
      res.setHeader('Cache-Control', 'private, max-age=3600');
      await pipeline(Readable.fromWeb(media.body as any), res);
    } catch {
      if (!res.headersSent) res.status(502).json({ error: 'Clip unavailable' });
      else res.destroy();
    } finally { clearTimeout(timer); }
    return;
  }
  if (req.method === 'GET' && parts.length === 1 && parts[0] === 'snapshot') {
    const streamRequest = upstream(`${ingest()}/v1/streams`);
    const requests = {
      health: () => upstream(`${ingest()}/v1/health/ready`),
      assistant: () => upstream(`${analytics()}/v1/assistant/health`),
      streams: () => streamRequest,
      rules: () => upstream(`${analytics()}/v1/rules`),
      jobs: () => upstream(`${analytics()}/v1/reviews`),
      incidents: async () => {
        const streams = await streamRequest;
        const active = streams.find((stream: any) => stream.config?.enabled);
        return upstream(`${analytics()}/v1/incidents?limit=100${active ? `&stream_id=${encodeURIComponent(active.id)}` : ''}`);
      },
      sources: () => upstream(`${analytics()}/v1/demo/sources`),
      ...Object.fromEntries(['decisions', 'events', 'reviews'].map(kind => [kind, async () => {
        const data = await upstream(`${elastic()}/d1-${kind}-*/_search`, 'POST', {
          size: 20, sort: [{ '@timestamp': { order: 'desc', unmapped_type: 'date' } }],
        });
        return data.hits.hits.map((hit: { _source: unknown }) => hit._source);
      }])),
    };
    const entries = await Promise.all(Object.entries(requests).map(async ([key, call]) => {
      try { return [key, { data: await call() }]; }
      catch (error) { return [key, { error: error instanceof Error ? error.message : 'Service unavailable' }]; }
    }));
    return res.status(200).json({ fetched_at: new Date().toISOString(), ...Object.fromEntries(entries) });
  }
  const questionPath = parts[0] === 'reviews' && /^[a-zA-Z0-9_-]{1,100}$/.test(parts[1] || '') && parts[2] === 'questions';
  const videoQuestions = questionPath && parts.length === 3;
  const questionStatus = questionPath && parts.length === 4 && /^[a-zA-Z0-9_-]{1,100}$/.test(parts[3]);
  const demoSources = parts.length === 2 && parts[0] === 'demo' && parts[1] === 'sources';
  if (((videoQuestions || demoSources) && ['GET', 'POST'].includes(req.method || '')) || (questionStatus && req.method === 'GET')) {
    if (req.method === 'POST') {
      if (!req.headers['content-type']?.startsWith('application/json')) return res.status(415).json({ error: 'JSON required' });
      try { if (req.headers.origin && new URL(req.headers.origin).host !== req.headers.host) return res.status(403).json({ error: 'Origin mismatch' }); }
      catch { return res.status(403).json({ error: 'Invalid origin' }); }
    }
    try {
      const result = await fetch(`${analytics()}/v1/${parts.join('/')}`, {
        method: req.method, headers: { 'Content-Type': 'application/json' },
        body: req.method === 'POST' ? JSON.stringify(req.body) : undefined,
        signal: AbortSignal.timeout(videoQuestions || questionStatus ? 10000 : 30000), redirect: 'error', cache: 'no-store',
      });
      const body = await result.json();
      const retryAfter = result.headers.get('retry-after');
      if (retryAfter && /^\d{1,5}$/.test(retryAfter)) res.setHeader('Retry-After', retryAfter);
      return res.status(result.status).json(result.ok ? body : { error: typeof body.detail === 'string' ? body.detail : 'The request could not be completed.' });
    } catch { return res.status(502).json({ error: videoQuestions || questionStatus ? 'Cosmos connection interrupted. Retry to check this question.' : 'The source could not be switched. Check the active source before retrying.' }); }
  }
  if (req.method === 'POST' && parts.length === 2 && parts[0] === 'assistant' && ['draft', 'apply'].includes(parts[1])) {
    if (!req.headers['content-type']?.startsWith('application/json')) return res.status(415).json({ error: 'JSON required' });
    try {
      if (req.headers.origin && new URL(req.headers.origin).host !== req.headers.host) return res.status(403).json({ error: 'Origin mismatch' });
    } catch { return res.status(403).json({ error: 'Invalid origin' }); }
    try {
      const result = await fetch(`${analytics()}/v1/assistant/${parts[1]}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req.body),
        signal: AbortSignal.timeout(135000), redirect: 'error',
      });
      const body = await result.json();
      return res.status(result.status).json(result.ok ? body : { error: typeof body.detail === 'string' ? body.detail : 'Invalid configuration. Check the fields and try again.' });
    } catch { return res.status(502).json({ error: 'The local assistant is unavailable. Your draft has not been applied.' }); }
  }
  if (req.method === 'PUT' && parts.length === 2 && ['streams', 'rules'].includes(parts[0]) && /^[a-zA-Z0-9_-]{1,64}$/.test(parts[1])) {
    // Require same-origin JSON, including for requests coming through an SSH tunnel.
    if (!req.headers['content-type']?.startsWith('application/json')) return res.status(415).json({ error: 'JSON required' });
    try {
      if (req.headers.origin && new URL(req.headers.origin).host !== req.headers.host) return res.status(403).json({ error: 'Origin mismatch' });
    } catch { return res.status(403).json({ error: 'Invalid origin' }); }
    const base = parts[0] === 'streams' ? ingest() : analytics();
    try { return res.status(200).json(await upstream(`${base}/v1/${parts[0]}/${parts[1]}`, 'PUT', req.body)); }
    catch (error) { return res.status(502).json({ error: error instanceof Error ? error.message : 'Save failed' }); }
  }
  return res.status(405).json({ error: 'Unsupported operation' });
}
export const config = { api: { bodyParser: { sizeLimit: '32kb' }, responseLimit: false } };
