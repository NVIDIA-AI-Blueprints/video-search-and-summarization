// SPDX-License-Identifier: MIT
import {
  uploadFileChunkedViaAgent,
  notifyGenericUploadComplete,
} from '../../lib-src/utils/videoUpload';

// Minimal XMLHttpRequest double — see chunkedUpload.test.ts in
// packages/nv-metropolis-bp-vss-ui/video-management for the shared pattern.
class MockXHR {
  static instances: MockXHR[] = [];
  public upload = { addEventListener: jest.fn() };
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
    (this.listeners.abort || []).forEach((cb) => cb());
  }

  finish(status: number, responseText: string) {
    this.driven = true;
    this.status = status;
    this.responseText = responseText;
    (this.listeners.load || []).forEach((cb) => cb());
  }
}

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

describe('notifyGenericUploadComplete', () => {
  let fetchMock: jest.Mock;

  beforeEach(() => {
    fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    global.fetch = fetchMock;
  });

  it('POSTs to /videos/{basename}/complete (generic path, not /videos-for-search/)', async () => {
    await notifyGenericUploadComplete(
      'https://agent.example.com/api/v1',
      'my_video.mp4',
      { sensorId: 'sensor-1' } as any,
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://agent.example.com/api/v1/videos/my_video/complete');
    expect(url).not.toContain('videos-for-search');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
  });

  it('forwards the full upload response as the request body', async () => {
    const response = { sensorId: 'sensor-1', filename: 'foo', bytes: 1024, filePath: '/tmp/foo.mp4' };
    await notifyGenericUploadComplete('https://agent.example.com', 'foo.mp4', response as any);

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual(response);
  });

  it('surfaces agent-side error detail strings', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => ({ detail: 'VST timeout' }),
    });

    await expect(
      notifyGenericUploadComplete('https://agent.example.com', 'x.mp4', { sensorId: 'x' } as any),
    ).rejects.toThrow('VST timeout');
  });

  it('attaches non-empty formData as a top-level custom_params field', async () => {
    const response = { sensorId: 'sensor-1' };
    await notifyGenericUploadComplete(
      'https://agent.example.com',
      'foo.mp4',
      response as any,
      { embedding: true, language: 'en' },
    );

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ ...response, custom_params: { embedding: true, language: 'en' } });
  });

  it('omits custom_params entirely when formData is empty or undefined', async () => {
    const response = { sensorId: 'sensor-1' };

    await notifyGenericUploadComplete('https://agent.example.com', 'a.mp4', response as any, {});
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).not.toHaveProperty('custom_params');

    await notifyGenericUploadComplete('https://agent.example.com', 'b.mp4', response as any, undefined);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).not.toHaveProperty('custom_params');
  });
});

describe('uploadFileChunkedViaAgent', () => {
  beforeEach(() => {
    MockXHR.instances = [];
    (globalThis as any).XMLHttpRequest = MockXHR;
    global.fetch = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
  });

  it('sends chunks to /videos/chunked/upload and finishes with notifyGenericUploadComplete', async () => {
    const file = new File(['x'.repeat(25)], 'chat_video.mp4', { type: 'video/mp4' });
    const agentUrl = 'https://agent.example.com/api/v1';

    const promise = uploadFileChunkedViaAgent(file, agentUrl, {}, undefined, undefined);

    // 25 bytes with chunkSize=10 would be 3 chunks, but we pass default (10MB) so one chunk.
    await flushAndFinish(200, JSON.stringify({
      sensorId: 'chat-sensor-1',
      filename: 'chat_video',
      bytes: 25,
      filePath: '/tmp/chat_video.mp4',
    }));

    const result = await promise;

    // Chunk POSTed to the agent proxy URL, not directly to VST.
    expect(MockXHR.instances).toHaveLength(1);
    expect(MockXHR.instances[0].url).toBe('https://agent.example.com/api/v1/videos/chunked/upload');
    expect(MockXHR.instances[0].headers['nvstreamer-chunk-number']).toBe('1');
    expect(MockXHR.instances[0].headers['nvstreamer-is-last-chunk']).toBe('true');

    // Then /videos/{basename}/complete fired with the VST response as body.
    expect((global.fetch as jest.Mock)).toHaveBeenCalledWith(
      'https://agent.example.com/api/v1/videos/chat_video/complete',
      expect.objectContaining({ method: 'POST' }),
    );

    // Return shape mirrors uploadFile's FileUploadResult so callers can swap.
    expect(result.sensorId).toBe('chat-sensor-1');
    expect(result.filename).toBe('chat_video');
    expect(result.bytes).toBe(25);
  });

  it('uses requestFilename override when provided for the /complete call path', async () => {
    const file = new File(['y'.repeat(10)], 'original.mp4');

    const promise = uploadFileChunkedViaAgent(
      file,
      'https://agent.example.com/api/v1',
      {},
      undefined,
      undefined,
      'renamed.mp4',
    );

    await flushAndFinish(200, JSON.stringify({ sensorId: 's1' }));
    await promise;

    const completeUrl = (global.fetch as jest.Mock).mock.calls[0][0];
    expect(completeUrl).toBe('https://agent.example.com/api/v1/videos/renamed/complete');
  });

  it('forwards non-empty formData to /complete as custom_params', async () => {
    const file = new File(['z'.repeat(10)], 'chat_video.mp4');
    const promise = uploadFileChunkedViaAgent(
      file,
      'https://agent.example.com/api/v1',
      { embedding: true, language: 'en' },
    );

    await flushAndFinish(200, JSON.stringify({ sensorId: 's1' }));
    await promise;

    const body = JSON.parse((global.fetch as jest.Mock).mock.calls[0][1].body);
    expect(body.custom_params).toEqual({ embedding: true, language: 'en' });
  });
});
