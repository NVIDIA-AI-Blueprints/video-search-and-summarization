// SPDX-License-Identifier: MIT
import {
  getUploadUrl,
  uploadFileChunked,
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

describe('getUploadUrl', () => {
  let fetchMock: jest.Mock;

  beforeEach(() => {
    fetchMock = jest.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ url: 'https://vst.example.com/v1/storage/file' }),
    });
    global.fetch = fetchMock;
  });

  it('POSTs to {agent}/videos with the filename and returns the agent URL', async () => {
    const url = await getUploadUrl('chat_video.mp4', 'https://agent.example.com/api/v1');

    const [callUrl, init] = fetchMock.mock.calls[0];
    expect(callUrl).toBe('https://agent.example.com/api/v1/videos');
    expect(init.method).toBe('POST');
    expect(JSON.parse(init.body)).toEqual({ filename: 'chat_video.mp4' });
    expect(url).toBe('https://vst.example.com/v1/storage/file');
  });

  it('surfaces agent-side error detail strings', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      statusText: 'Bad Request',
      json: async () => ({ detail: 'Filename cannot contain whitespace' }),
    });

    await expect(
      getUploadUrl('bad name.mp4', 'https://agent.example.com/api/v1'),
    ).rejects.toThrow('Filename cannot contain whitespace');
  });

  it('throws when the agent response is missing "url"', async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({}),
    });

    await expect(
      getUploadUrl('clip.mp4', 'https://agent.example.com/api/v1'),
    ).rejects.toThrow(/missing "url"/);
  });
});

describe('notifyGenericUploadComplete', () => {
  let fetchMock: jest.Mock;

  beforeEach(() => {
    fetchMock = jest.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({}) });
    global.fetch = fetchMock;
  });

  it('POSTs to /videos/{sensorId}/complete using the VST sensor id as the path param', async () => {
    await notifyGenericUploadComplete(
      'https://agent.example.com/api/v1',
      'sensor-1',
      'my_video.mp4',
      { sensorId: 'sensor-1' } as any,
    );

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe('https://agent.example.com/api/v1/videos/sensor-1/complete');
    expect(url).not.toContain('videos-for-search');
    expect(init.method).toBe('POST');
    expect(init.headers).toEqual({ 'Content-Type': 'application/json' });
  });

  it('forwards the upload response plus filename as the request body', async () => {
    const response = { sensorId: 'sensor-1', bytes: 1024, filePath: '/tmp/foo.mp4' };
    await notifyGenericUploadComplete(
      'https://agent.example.com',
      'sensor-1',
      'foo.mp4',
      response as any,
    );

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body).toEqual({ ...response, filename: 'foo.mp4' });
  });

  it('throws when called without a sensor id (caller bug)', async () => {
    await expect(
      notifyGenericUploadComplete('https://agent.example.com', '', 'foo.mp4', { sensorId: '' } as any),
    ).rejects.toThrow(/sensorId/);
  });

  it('surfaces agent-side error detail strings', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      json: async () => ({ detail: 'VST timeout' }),
    });

    await expect(
      notifyGenericUploadComplete('https://agent.example.com', 'sensor-1', 'x.mp4', { sensorId: 'sensor-1' } as any),
    ).rejects.toThrow('VST timeout');
  });

  it('attaches non-empty formData as a top-level custom_params field', async () => {
    const response = { sensorId: 'sensor-1' };
    await notifyGenericUploadComplete(
      'https://agent.example.com',
      'sensor-1',
      'foo.mp4',
      response as any,
      { embedding: true, language: 'en' },
    );

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.custom_params).toEqual({ embedding: true, language: 'en' });
  });

  it('omits custom_params entirely when formData is empty or undefined', async () => {
    const response = { sensorId: 'sensor-1' };

    await notifyGenericUploadComplete('https://agent.example.com', 'sensor-1', 'a.mp4', response as any, {});
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).not.toHaveProperty('custom_params');

    await notifyGenericUploadComplete('https://agent.example.com', 'sensor-1', 'b.mp4', response as any, undefined);
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).not.toHaveProperty('custom_params');
  });
});

describe('uploadFileChunked', () => {
  beforeEach(() => {
    MockXHR.instances = [];
    (globalThis as any).XMLHttpRequest = MockXHR;
    global.fetch = jest.fn(async (url: string) => {
      if (typeof url === 'string' && url.endsWith('/videos')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ url: 'https://vst.example.com/v1/storage/file' }),
        };
      }
      return { ok: true, status: 200, json: async () => ({}) };
    }) as any;
  });

  it('does the three-step handshake: agent /videos → chunks to VST → agent /videos/{id}/complete', async () => {
    const file = new File(['x'.repeat(25)], 'chat_video.mp4', { type: 'video/mp4' });
    const agentUrl = 'https://agent.example.com/api/v1';

    const promise = uploadFileChunked(file, agentUrl, {}, undefined, undefined);

    await flushAndFinish(200, JSON.stringify({
      sensorId: 'chat-sensor-1',
      filename: 'chat_video',
      bytes: 25,
      filePath: '/tmp/chat_video.mp4',
    }));

    const result = await promise;

    // Step 1: agent told the UI where to upload.
    const fetchMock = global.fetch as jest.Mock;
    expect(fetchMock.mock.calls[0][0]).toBe('https://agent.example.com/api/v1/videos');
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ filename: 'chat_video.mp4' });

    // Step 2: chunk POSTed to the URL the agent returned (not directly to a
    // hardcoded VST URL — the UI doesn't even know vst's address).
    expect(MockXHR.instances).toHaveLength(1);
    expect(MockXHR.instances[0].url).toBe('https://vst.example.com/v1/storage/file');
    expect(MockXHR.instances[0].headers['nvstreamer-chunk-number']).toBe('1');
    expect(MockXHR.instances[0].headers['nvstreamer-is-last-chunk']).toBe('true');

    // Step 3: /complete fired against the agent with VST's stream id in the path
    // and the filename in the body.
    expect(fetchMock.mock.calls[1][0]).toBe('https://agent.example.com/api/v1/videos/chat-sensor-1/complete');
    const completeBody = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(completeBody.filename).toBe('chat_video.mp4');
    expect(completeBody.sensorId).toBe('chat-sensor-1');

    expect(result.sensorId).toBe('chat-sensor-1');
    expect(result.filename).toBe('chat_video');
    expect(result.bytes).toBe(25);
  });

  it('uses requestFilename override when provided', async () => {
    const file = new File(['y'.repeat(10)], 'original.mp4');

    const promise = uploadFileChunked(
      file,
      'https://agent.example.com/api/v1',
      {},
      undefined,
      undefined,
      'renamed.mp4',
    );

    await flushAndFinish(200, JSON.stringify({ sensorId: 's1' }));
    await promise;

    const fetchMock = global.fetch as jest.Mock;
    // /videos called with the override filename
    expect(JSON.parse(fetchMock.mock.calls[0][1].body).filename).toBe('renamed.mp4');
    // The actual nvstreamer upload also uses the override filename.
    expect(MockXHR.instances[0].headers['nvstreamer-file-name']).toBe('renamed.mp4');
    // /complete body carries the override filename
    expect(JSON.parse(fetchMock.mock.calls[1][1].body).filename).toBe('renamed.mp4');
  });

  it('forwards non-empty formData to /complete as custom_params', async () => {
    const file = new File(['z'.repeat(10)], 'chat_video.mp4');
    const promise = uploadFileChunked(
      file,
      'https://agent.example.com/api/v1',
      { embedding: true, language: 'en' },
    );

    await flushAndFinish(200, JSON.stringify({ sensorId: 's1' }));
    await promise;

    const fetchMock = global.fetch as jest.Mock;
    const body = JSON.parse(fetchMock.mock.calls[1][1].body);
    expect(body.custom_params).toEqual({ embedding: true, language: 'en' });
  });
});
