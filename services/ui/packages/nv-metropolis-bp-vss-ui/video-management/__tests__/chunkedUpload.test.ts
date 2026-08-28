// SPDX-License-Identifier: MIT
import { chunkedUpload } from '../lib-src/chunkedUpload';

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

describe('chunkedUpload', () => {
  beforeEach(installMockXHR);

  it('single-chunk upload: sets nvstreamer headers and returns parsed response', async () => {
    const file = new File(['x'.repeat(1024)], 'small.mp4', { type: 'video/mp4' });
    const promise = chunkedUpload({
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
    const promise = chunkedUpload({ file, uploadUrl: 'http://vst/u', chunkSize: 10 });

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
    const promise = chunkedUpload({ file, uploadUrl: 'http://vst/u', chunkSize: 10, maxRetries: 2 });

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
    const promise = chunkedUpload({ file, uploadUrl: 'http://vst/u', chunkSize: 10 });

    await flushAndFinish(200, JSON.stringify({ chunkCount: '1' /* no sensorId */ }));

    await expect(promise).rejects.toThrow(/sensorId/);
  });

  it('cancels via AbortSignal before first chunk starts', async () => {
    const file = new File(['a'.repeat(10)], 'cancel.mp4', { type: 'video/mp4' });
    const ctrl = new AbortController();
    ctrl.abort();

    await expect(
      chunkedUpload({ file, uploadUrl: 'http://vst/u', chunkSize: 10, abortSignal: ctrl.signal })
    ).rejects.toThrow(/cancelled/i);
  });
});
