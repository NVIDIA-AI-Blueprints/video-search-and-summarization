// SPDX-License-Identifier: MIT
import { uploadFileChunked, notifyUploadComplete } from '../lib-src/chunkedUpload';

// Minimal XMLHttpRequest double that the test drives explicitly. Each `new
// MockXHR()` instance is captured via the `send` spy so tests can inspect
// which headers/body were sent and then invoke `finish()` / `fail()` to
// resolve the surrounding promise.
class MockXHR {
  static instances: MockXHR[] = [];
  public upload = { addEventListener: jest.fn() };
  public readyState = 0;
  public status = 0;
  public responseText = '';
  public headers: Record<string, string> = {};
  public body: any = null;
  public method = '';
  public url = '';
  public sendCalled = false;
  public driven = false;
  private listeners: Record<string, Array<() => void>> = {};

  constructor() {
    MockXHR.instances.push(this);
  }

  addEventListener(event: string, cb: () => void) {
    (this.listeners[event] ??= []).push(cb);
  }

  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }

  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }

  send(body: any) {
    this.body = body;
    this.sendCalled = true;
  }

  abort() {
    this.driven = true;
    this.status = 0;
    (this.listeners.abort || []).forEach((cb) => cb());
  }

  finish(status: number, responseText: string) {
    this.driven = true;
    this.status = status;
    this.responseText = responseText;
    (this.listeners.load || []).forEach((cb) => cb());
  }

  fail() {
    this.driven = true;
    (this.listeners.error || []).forEach((cb) => cb());
  }
}

const installMockXHR = () => {
  MockXHR.instances = [];
  (globalThis as any).XMLHttpRequest = MockXHR;
};

// Wait for the next XHR instance that has called send() but hasn't been
// driven yet, then finish it. Polls microtasks so the caller's await-chain
// has time to create chunk N+1 after chunk N resolves.
const flushAndFinish = async (status: number, responseBody: string) => {
  for (let i = 0; i < 20; i++) {
    const next = MockXHR.instances.find((x) => x.sendCalled && !x.driven);
    if (next) {
      next.finish(status, responseBody);
      return;
    }
    await Promise.resolve();
  }
  throw new Error('flushAndFinish: no pending XHR found');
};

describe('uploadFileChunked', () => {
  beforeEach(installMockXHR);

  it('single-chunk upload: sets nvstreamer headers and returns parsed response', async () => {
    const file = new File(['x'.repeat(1024)], 'small.mp4', { type: 'video/mp4' });
    const promise = uploadFileChunked({
      file,
      uploadUrl: 'http://vst/storage/file',
      chunkSize: 10 * 1024 * 1024, // way larger than the file
    });

    await flushAndFinish(200, JSON.stringify({ sensorId: 'sensor-abc', filename: 'small', bytes: 1024 }));
    const res = await promise;

    expect(res.sensorId).toBe('sensor-abc');
    expect(MockXHR.instances).toHaveLength(1);
    const xhr = MockXHR.instances[0];
    expect(xhr.method).toBe('POST');
    expect(xhr.url).toBe('http://vst/storage/file');
    expect(xhr.headers['nvstreamer-chunk-number']).toBe('1');
    expect(xhr.headers['nvstreamer-total-chunks']).toBe('1');
    expect(xhr.headers['nvstreamer-is-last-chunk']).toBe('true');
    expect(xhr.headers['nvstreamer-file-name']).toBe('small.mp4');
    // identifier should be a uuid-shaped string
    expect(xhr.headers['nvstreamer-identifier']).toMatch(/^[0-9a-f-]{20,}$/i);
  });

  it('multi-chunk: sends total-chunks, marks last chunk, reuses identifier', async () => {
    // 25-byte file, chunkSize=10 → 3 chunks (10, 10, 5)
    const file = new File(['x'.repeat(25)], 'multi.mp4', { type: 'video/mp4' });
    const promise = uploadFileChunked({ file, uploadUrl: 'http://vst/u', chunkSize: 10 });

    for (let i = 1; i <= 3; i++) {
      await flushAndFinish(200, JSON.stringify(i === 3 ? { sensorId: 'sensor-xyz' } : { chunkCount: String(i) }));
    }
    const res = await promise;

    expect(res.sensorId).toBe('sensor-xyz');
    expect(MockXHR.instances).toHaveLength(3);
    const ids = MockXHR.instances.map((x) => x.headers['nvstreamer-identifier']);
    expect(new Set(ids).size).toBe(1); // same identifier across chunks
    expect(MockXHR.instances[0].headers['nvstreamer-is-last-chunk']).toBe('false');
    expect(MockXHR.instances[1].headers['nvstreamer-is-last-chunk']).toBe('false');
    expect(MockXHR.instances[2].headers['nvstreamer-is-last-chunk']).toBe('true');
    expect(MockXHR.instances.every((x) => x.headers['nvstreamer-total-chunks'] === '3')).toBe(true);
  });

  it('retries a failed chunk up to maxRetries and succeeds on retry', async () => {
    jest.useFakeTimers();
    const file = new File(['y'.repeat(10)], 'retry.mp4', { type: 'video/mp4' });
    const promise = uploadFileChunked({ file, uploadUrl: 'http://vst/u', chunkSize: 10, maxRetries: 2 });

    // First attempt fails with a network error
    await Promise.resolve();
    MockXHR.instances[0].fail();

    // Backoff is 1s before retry; advance timers
    await jest.advanceTimersByTimeAsync(1000);
    await flushAndFinish(200, JSON.stringify({ sensorId: 'sensor-retry' }));

    const res = await promise;
    expect(res.sensorId).toBe('sensor-retry');
    expect(MockXHR.instances).toHaveLength(2);
    jest.useRealTimers();
  });

  it('throws when final-chunk response lacks sensorId (runtime guard)', async () => {
    const file = new File(['z'.repeat(10)], 'nosensor.mp4', { type: 'video/mp4' });
    const promise = uploadFileChunked({ file, uploadUrl: 'http://vst/u', chunkSize: 10 });

    await flushAndFinish(200, JSON.stringify({ chunkCount: '1' /* no sensorId */ }));

    await expect(promise).rejects.toThrow(/sensorId/);
  });

  it('cancels via AbortSignal before first chunk starts', async () => {
    const file = new File(['a'.repeat(10)], 'cancel.mp4', { type: 'video/mp4' });
    const ctrl = new AbortController();
    ctrl.abort();

    await expect(
      uploadFileChunked({ file, uploadUrl: 'http://vst/u', chunkSize: 10, abortSignal: ctrl.signal })
    ).rejects.toThrow(/cancelled/i);
  });
});

describe('notifyUploadComplete', () => {
  let fetchMock: jest.Mock;

  beforeEach(() => {
    fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    });
    global.fetch = fetchMock;
  });

  // Minimal valid upload response shape for tests that don't care about the body content
  const uploadResponse = { sensorId: 's1' } as any;

  it('POSTs to videos/{basename}/complete forwarding the full upload response', async () => {
    const response = { sensorId: 'sensor-123', filename: 'my clip', bytes: 100 } as any;
    await notifyUploadComplete('https://agent.example.com/api/v1', 'my clip.mp4', response);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe(
      'https://agent.example.com/api/v1/videos/my%20clip/complete',
    );
    expect(init?.method).toBe('POST');
    expect(init?.headers).toEqual({ 'Content-Type': 'application/json' });
    expect(JSON.parse(init?.body as string)).toEqual(response);
  });

  it('strips trailing slash from agentApiUrl before appending path', async () => {
    await notifyUploadComplete('https://agent.example.com/api/v1/', 'video.mp4', uploadResponse);

    expect(fetchMock.mock.calls[0][0]).toBe(
      'https://agent.example.com/api/v1/videos/video/complete',
    );
  });

  it('uses full filename when there is no extension (no dot)', async () => {
    await notifyUploadComplete('https://agent.example.com', 'README', uploadResponse);

    expect(fetchMock.mock.calls[0][0]).toBe(
      'https://agent.example.com/videos/README/complete',
    );
  });

  it('uses basename before last dot for multi-dot names', async () => {
    await notifyUploadComplete('https://agent.example.com', 'a.b.c.mp4', uploadResponse);

    expect(fetchMock.mock.calls[0][0]).toBe(
      'https://agent.example.com/videos/a.b.c/complete',
    );
  });

  it('forwards AbortSignal to fetch when provided', async () => {
    const controller = new AbortController();
    await notifyUploadComplete('https://agent.example.com', 'f.mp4', uploadResponse, undefined, controller.signal);

    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.signal).toBe(controller.signal);
  });

  it('includes formData as custom_params when provided', async () => {
    const formData = { tags: 'warehouse,indoor', priority: 'high' };
    await notifyUploadComplete('https://agent.example.com', 'f.mp4', uploadResponse, formData);

    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.custom_params).toEqual(formData);
    // Upload response fields are still present at the top level
    expect(body.sensorId).toBe('s1');
  });

  it('omits custom_params entirely when formData is empty or undefined', async () => {
    await notifyUploadComplete('https://agent.example.com', 'f.mp4', uploadResponse);
    let body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect('custom_params' in body).toBe(false);

    fetchMock.mockClear();
    await notifyUploadComplete('https://agent.example.com', 'f.mp4', uploadResponse, {});
    body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect('custom_params' in body).toBe(false);
  });

  it('throws with status message when response is not ok and body is not JSON', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => {
        throw new SyntaxError('not json');
      },
    });

    await expect(
      notifyUploadComplete('https://agent.example.com', 'f.mp4', uploadResponse),
    ).rejects.toThrow('Post-processing failed with status 502');
  });

  it('throws detail string from error JSON when response is not ok', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: 'embedding failed' }),
    });

    await expect(
      notifyUploadComplete('https://agent.example.com', 'f.mp4', uploadResponse),
    ).rejects.toThrow('embedding failed');
  });

  it('throws stringified detail when detail is not a string', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      json: async () => ({ detail: [{ loc: ['body'], msg: 'invalid' }] }),
    });

    await expect(
      notifyUploadComplete('https://agent.example.com', 'f.mp4', uploadResponse),
    ).rejects.toThrow(JSON.stringify([{ loc: ['body'], msg: 'invalid' }]));
  });
});
