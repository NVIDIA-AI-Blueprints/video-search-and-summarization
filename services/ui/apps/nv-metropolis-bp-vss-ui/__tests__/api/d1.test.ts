/** @jest-environment node */
// SPDX-FileCopyrightText: Copyright (c) 2026 D1 project contributors
// SPDX-License-Identifier: Apache-2.0
import handler from '../../pages/api/d1/[...path]';
function response() {
  const res: any = { statusCode: 200, body: undefined, setHeader: jest.fn() };
  res.status = (code: number) => { res.statusCode = code; return res; };
  res.json = (body: unknown) => { res.body = body; return res; };
  res.end = () => res;
  res.send = (body: unknown) => { res.body = body; return res; };
  return res;
}
const request = (method: string, path: string[], headers: any = {}) => ({ method, query: { path }, headers, body: { enabled: false } } as any);
afterEach(() => { delete process.env.D1_AGENT_ENABLED; jest.restoreAllMocks(); });
test('disabled integration cannot read or mutate services', async () => {
  const call = jest.spyOn(global, 'fetch'); const res = response();
  await handler(request('PUT', ['rules', 'beach']), res);
  expect(res.statusCode).toBe(404); expect(call).not.toHaveBeenCalled();
});
test('preview binds to a validated stream revision and forwards fresh JPEG bytes without caching', async () => {
  process.env.D1_AGENT_ENABLED = 'true';
  const bytes = new Uint8Array([255, 216, 255, 217]);
  const call = jest.spyOn(global, 'fetch').mockResolvedValue(new Response(bytes, { headers: {
    'content-type': 'image/jpeg', 'x-frame-sequence': '42', 'x-stream-revision': '7',
  } }));
  const req = request('GET', ['preview', 'traffic-recorded']); req.query.revision = '7';
  const res = response(); await handler(req, res);
  expect(call).toHaveBeenCalledWith('http://127.0.0.1:8020/v1/streams/traffic-recorded/preview?revision=7', expect.objectContaining({ cache: 'no-store', redirect: 'error' }));
  expect(res.statusCode).toBe(200); expect(res.body).toEqual(Buffer.from(bytes));
  expect(res.setHeader).toHaveBeenCalledWith('Cache-Control', 'no-store');
  expect(res.setHeader).toHaveBeenCalledWith('x-frame-sequence', '42');
});
test('preview rejects arbitrary paths and invalid revisions before contacting ingest', async () => {
  process.env.D1_AGENT_ENABLED = 'true'; const call = jest.spyOn(global, 'fetch');
  for (const [stream, revision] of [['..','1'], ['traffic','1&url=http://other'], ['traffic','0'], ['traffic',undefined]]) {
    const req = request('GET', ['preview', stream!]); req.query.revision = revision;
    const res = response(); await handler(req, res); expect([400,405]).toContain(res.statusCode);
  }
  expect(call).not.toHaveBeenCalled();
});
test('preview preserves unavailable input and rejects non-image upstream responses', async () => {
  process.env.D1_AGENT_ENABLED = 'true';
  const call = jest.spyOn(global, 'fetch');
  const req = request('GET', ['preview', 'traffic']); req.query.revision = '1';
  for (const status of [404,409,503]) {
    call.mockResolvedValueOnce(new Response('not ready', { status }));
    const res = response(); await handler(req, res); expect(res.statusCode).toBe(status);
    expect(res.setHeader).toHaveBeenCalledWith('Cache-Control','no-store');
  }
  call.mockResolvedValueOnce(new Response('not an image', { headers: { 'content-type':'application/json' } }));
  const res = response(); await handler(req,res); expect(res.statusCode).toBe(502);
});
test('rejects cross-origin writes and arbitrary proxy paths before fetch', async () => {
  process.env.D1_AGENT_ENABLED = 'true'; const call = jest.spyOn(global, 'fetch');
  const cross = response(); await handler(request('PUT', ['streams', 'beach'], { 'content-type': 'application/json', host: 'localhost:3000', origin: 'https://other.example' }), cross);
  expect(cross.statusCode).toBe(403);
  const chatCross = response(); await handler(request('POST', ['assistant', 'apply'], { 'content-type': 'application/json', host: 'localhost:3000', origin: 'https://other.example' }), chatCross);
  expect(chatCross.statusCode).toBe(403);
  const invalid = response(); await handler(request('PUT', ['rules', '..'], { 'content-type': 'application/json' }), invalid);
  expect(invalid.statusCode).toBe(405); expect(call).not.toHaveBeenCalled();
});
test('snapshot preserves successful data when one service is down', async () => {
  process.env.D1_AGENT_ENABLED = 'true';
  jest.spyOn(global, 'fetch').mockImplementation(async (url) => {
    if (String(url).endsWith('/v1/rules')) throw new Error('Service unavailable');
    return { ok: true, json: async () => String(url).includes('_search') ? { hits: { hits: [{ _source: { id: 'real-record' } }] } } : [] } as Response;
  });
  const res = response(); await handler(request('GET', ['snapshot']), res);
  expect(res.statusCode).toBe(200); expect(res.body.rules.error).toBe('Service unavailable');
  expect(res.body.reviews.data).toEqual([{ id: 'real-record' }]);
});

test('clip route forwards ranges and streams bytes from only the analytics service', async () => {
  const { Writable } = await import('node:stream');
  process.env.D1_AGENT_ENABLED = 'true';
  const call = jest.spyOn(global, 'fetch').mockResolvedValue(new Response(new Uint8Array([1, 2, 3]), {
    status: 206, headers: { 'content-type': 'video/mp4', 'content-range': 'bytes 0-2/10', 'content-length': '3' },
  }));
  const chunks: Buffer[] = [];
  const res: any = new Writable({ write(chunk, _encoding, callback) { chunks.push(Buffer.from(chunk)); callback(); } });
  const headers: Record<string, string> = {};
  res.setHeader = (key: string, value: string) => { headers[key] = value; };
  res.status = (code: number) => { res.statusCode = code; return res; };
  await handler(request('GET', ['clips', 'event-123'], { range: 'bytes=0-2' }), res);
  expect(call).toHaveBeenCalledWith('http://127.0.0.1:8021/v1/reviews/event-123/clip', expect.objectContaining({ headers: { Range: 'bytes=0-2' } }));
  expect(res.statusCode).toBe(206); expect(headers['content-range']).toBe('bytes 0-2/10');
  expect(Buffer.concat(chunks)).toEqual(Buffer.from([1, 2, 3]));
});

test('video questions are bound to a validated event path and preserve busy errors', async () => {
 process.env.D1_AGENT_ENABLED='true';
 const call=jest.spyOn(global,'fetch').mockResolvedValue(new Response(JSON.stringify({detail:'Cosmos is busy; try again shortly.'}),{status:409,headers:{'content-type':'application/json'}}));
 const req=request('POST',['reviews','event-123','questions'],{'content-type':'application/json',host:'localhost:3000',origin:'http://localhost:3000'});req.body={question:'Is a collision visible?',request_id:'request-1'};
 const res=response();await handler(req,res);
 expect(call).toHaveBeenCalledWith('http://127.0.0.1:8021/v1/reviews/event-123/questions',expect.objectContaining({method:'POST',body:JSON.stringify(req.body),redirect:'error'}));
 expect(res.statusCode).toBe(409);expect(res.body.error).toContain('Cosmos is busy');
});
test('video questions and source changes reject cross-origin writes and arbitrary IDs',async()=>{
 process.env.D1_AGENT_ENABLED='true';const call=jest.spyOn(global,'fetch');
 for(const path of [['reviews','event-1','questions'],['demo','sources']]){const res=response();await handler(request('POST',path,{'content-type':'application/json',host:'localhost:3000',origin:'https://other.example'}),res);expect(res.statusCode).toBe(403);}
 const res=response();await handler(request('GET',['reviews','..','questions']),res);expect(res.statusCode).toBe(405);expect(call).not.toHaveBeenCalled();
});
test('queued questions return202 quickly and status polls preserve the event and request IDs', async () => {
  process.env.D1_AGENT_ENABLED = 'true';
  const queued = { id: 'request-1', event_id: 'event-123', question: 'Which lanes move?', status: 'queued' };
  const timeout = jest.spyOn(AbortSignal, 'timeout');
  const call = jest.spyOn(global, 'fetch').mockResolvedValueOnce(new Response(JSON.stringify(queued), { status: 202 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...queued, status: 'running' })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ ...queued, status: 'complete', answer: 'The far-left lanes are moving.' })));
  const req = request('POST', ['reviews', 'event-123', 'questions'], { 'content-type': 'application/json' });
  req.body = { question: queued.question, request_id: queued.id };
  const accepted = response(); await handler(req, accepted);
  expect(accepted.statusCode).toBe(202); expect(accepted.body).toEqual(queued);
  const running = response(); await handler(request('GET', ['reviews', 'event-123', 'questions', 'request-1']), running);
  expect(running.body.status).toBe('running');
  const complete = response(); await handler(request('GET', ['reviews', 'event-123', 'questions', 'request-1']), complete);
  expect(complete.body.answer).toBe('The far-left lanes are moving.');
  expect(call).toHaveBeenLastCalledWith('http://127.0.0.1:8021/v1/reviews/event-123/questions/request-1', expect.objectContaining({ method: 'GET', cache: 'no-store', redirect: 'error' }));
  expect(timeout.mock.calls).toEqual([[10000], [10000], [10000]]);
});
test('question status rejects unsafe IDs and unsupported writes before fetch', async () => {
  process.env.D1_AGENT_ENABLED = 'true'; const call = jest.spyOn(global, 'fetch');
  for (const path of [['reviews', 'event-1', 'questions', '..'], ['reviews', 'event-1', 'questions', 'request?url=other'], ['reviews', 'event-1', 'questions', 'request-1', 'extra']]) {
    const res = response(); await handler(request('GET', path), res); expect(res.statusCode).toBe(405);
  }
  const res = response(); await handler(request('POST', ['reviews', 'event-1', 'questions', 'request-1'], { 'content-type': 'application/json' }), res);
  expect(res.statusCode).toBe(405); expect(call).not.toHaveBeenCalled();
});
test('question queue capacity preserves429 and retry timing without claiming the question was answered', async () => {
  process.env.D1_AGENT_ENABLED = 'true';
  jest.spyOn(global, 'fetch').mockResolvedValue(new Response(JSON.stringify({ detail: 'Question queue is full. Try again shortly.' }), { status: 429, headers: { 'retry-after': '5' } }));
  const res = response(); await handler(request('POST', ['reviews', 'event-123', 'questions'], { 'content-type': 'application/json' }), res);
  expect(res.statusCode).toBe(429); expect(res.body.error).toContain('queue is full');
  expect(res.setHeader).toHaveBeenCalledWith('Retry-After', '5');
});
test('snapshot obtains authoritative incident grouping independently of Elasticsearch',async()=>{
 process.env.D1_AGENT_ENABLED='true';jest.spyOn(global,'fetch').mockImplementation(async url=>{if(String(url).includes('_search'))throw new Error('Index unavailable');return {ok:true,json:async()=>String(url).includes('/incidents')?{incidents:[{id:'grouped-one'}],candidates:[]}:[]} as Response;});
 const res=response();await handler(request('GET',['snapshot']),res);expect(res.body.incidents.data.incidents).toEqual([{id:'grouped-one'}]);expect(res.body.reviews.error).toBe('Index unavailable');
});
