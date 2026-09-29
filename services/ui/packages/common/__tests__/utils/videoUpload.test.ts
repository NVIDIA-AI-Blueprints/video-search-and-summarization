// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import { uploadFileChunked } from '../../lib-src/utils/videoUpload';

class MockXHR {
  static instances: MockXHR[] = [];
  public upload = { addEventListener: jest.fn() };
  public status = 0;
  public responseText = '';
  public headers: Record<string, string> = {};
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

  setRequestHeader(key: string, value: string) {
    this.headers[key] = value;
  }

  send() {
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
    const next = MockXHR.instances.find((xhr) => xhr.sendCalled && !xhr.driven);
    if (next) {
      next.finish(status, responseBody);
      return;
    }
    await Promise.resolve();
  }
  throw new Error('No pending XHR found');
};

describe('uploadFileChunked', () => {
  beforeEach(() => {
    MockXHR.instances = [];
    (globalThis as any).XMLHttpRequest = MockXHR;
    global.fetch = jest.fn();
  });

  it('uploads directly to the supplied VST URL without Agent requests', async () => {
    const file = new File(['x'.repeat(25)], 'chat_video.mp4', { type: 'video/mp4' });
    const promise = uploadFileChunked(
      file,
      'https://vst.example.com/v1/storage/file',
    );

    await flushAndFinish(200, JSON.stringify({
      sensorId: 'chat-sensor-1',
      filename: 'chat_video',
      bytes: 25,
      filePath: '/tmp/chat_video.mp4',
    }));

    const result = await promise;
    expect(global.fetch).not.toHaveBeenCalled();
    expect(MockXHR.instances).toHaveLength(1);
    expect(MockXHR.instances[0].url).toBe('https://vst.example.com/v1/storage/file');
    expect(MockXHR.instances[0].headers['nvstreamer-is-last-chunk']).toBe('true');
    expect(result.sensorId).toBe('chat-sensor-1');
    expect(result.filename).toBe('chat_video');
    expect(result.bytes).toBe(25);
  });

  it('uses the request filename override for the VST upload', async () => {
    const file = new File(['y'.repeat(10)], 'original.mp4');
    const promise = uploadFileChunked(
      file,
      'https://vst.example.com/v1/storage/file',
      undefined,
      undefined,
      'renamed.mp4',
    );

    await flushAndFinish(200, JSON.stringify({ sensorId: 's1' }));
    await promise;

    expect(MockXHR.instances[0].headers['nvstreamer-file-name']).toBe('renamed.mp4');
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('rejects a VST response without a sensorId', async () => {
    const file = new File(['z'], 'invalid.mp4');
    const promise = uploadFileChunked(
      file,
      'https://vst.example.com/v1/storage/file',
    );

    await flushAndFinish(200, JSON.stringify({ bytes: 1 }));
    await expect(promise).rejects.toThrow(/sensorId/);
  });

  it('honors cancellation before upload', async () => {
    const controller = new AbortController();
    controller.abort();

    await expect(uploadFileChunked(
      new File(['x'], 'cancel.mp4'),
      'https://vst.example.com/v1/storage/file',
      undefined,
      controller.signal,
    )).rejects.toThrow(/cancelled/i);
    expect(MockXHR.instances).toHaveLength(0);
  });
});

describe('opt-in upload retention protection', () => {
  const runtimeWindow = window as Window & { __ENV?: Record<string, string | undefined> };
  let originalRuntimeEnv: typeof runtimeWindow.__ENV;
  let originalProcessFlag: string | undefined;
  const uploadUrl = 'https://vst.example.com/vst/api/v1/storage/file';
  const returnedPath = '/home/vst/vst_release/streamer_videos/backend-renamed.mp4';
  const respond = (body: unknown, ok = true, status = 200) => ({
    ok, status, json: async () => body,
  });

  beforeEach(() => {
    MockXHR.instances = [];
    (globalThis as any).XMLHttpRequest = MockXHR;
    global.fetch = jest.fn();
    originalRuntimeEnv = runtimeWindow.__ENV;
    originalProcessFlag = process.env.NEXT_PUBLIC_PROTECT_UPLOADED_FILES;
    delete process.env.NEXT_PUBLIC_PROTECT_UPLOADED_FILES;
    runtimeWindow.__ENV = { NEXT_PUBLIC_PROTECT_UPLOADED_FILES: 'true' };
  });

  afterEach(() => {
    runtimeWindow.__ENV = originalRuntimeEnv;
    if (originalProcessFlag === undefined) delete process.env.NEXT_PUBLIC_PROTECT_UPLOADED_FILES;
    else process.env.NEXT_PUBLIC_PROTECT_UPLOADED_FILES = originalProcessFlag;
  });

  const acceptUpload = async (filePath: string | undefined = returnedPath) => {
    await flushAndFinish(200, JSON.stringify({ sensorId: 'new-upload', filePath }));
  };
  const startUpload = () => uploadFileChunked(new File(['video'], 'local-name.mp4'), uploadUrl);

  it.each([undefined, 'false'])('leaves protection unchanged when runtime flag is %s', async (flag) => {
    runtimeWindow.__ENV = { NEXT_PUBLIC_PROTECT_UPLOADED_FILES: flag };
    const pending = startUpload();
    await acceptUpload();
    await expect(pending).resolves.toMatchObject({ sensorId: 'new-upload' });
    expect(fetch).not.toHaveBeenCalled();
  });

  it('protects the exact returned path and verifies membership before succeeding', async () => {
    (fetch as jest.Mock)
      .mockResolvedValueOnce(respond({ invalidFiles: [] }))
      .mockResolvedValueOnce(respond([returnedPath]));
    const pending = startUpload();
    await acceptUpload();
    await expect(pending).resolves.toMatchObject({ filePath: returnedPath });
    expect(fetch).toHaveBeenNthCalledWith(1, uploadUrl + '/protect', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify({ filePath: [returnedPath], protect: true }),
    }));
    expect(fetch).toHaveBeenNthCalledWith(2, uploadUrl + '/protected', expect.objectContaining({
      cache: 'no-store',
    }));
    expect(MockXHR.instances).toHaveLength(1);
  });

  it('does not synthesize a path when the upload response omits it', async () => {
    const pending = startUpload();
    await flushAndFinish(200, JSON.stringify({ sensorId: 'new-upload' }));
    await expect(pending).rejects.toThrow('Upload completed, but retention protection could not be verified');
    expect(fetch).not.toHaveBeenCalled();
    expect(MockXHR.instances).toHaveLength(1);
  });

  it('reports rejected protection without uploading the accepted file again', async () => {
    (fetch as jest.Mock).mockResolvedValueOnce(respond({ invalidFiles: [returnedPath] }));
    const pending = startUpload();
    await acceptUpload();
    await expect(pending).rejects.toThrow('Do not upload again');
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(MockXHR.instances).toHaveLength(1);
  });

  it('does not report success when readback omits the uploaded file', async () => {
    (fetch as jest.Mock)
      .mockResolvedValueOnce(respond({ invalidFiles: [] }))
      .mockResolvedValueOnce(respond(['/another/file.mp4']));
    const pending = startUpload();
    await acceptUpload();
    await expect(pending).rejects.toThrow('absent from the protected-file list');
    expect(MockXHR.instances).toHaveLength(1);
  });

  it('reports HTTP protection failure without retrying upload', async () => {
    (fetch as jest.Mock).mockResolvedValueOnce(respond({}, false, 503));
    const pending = startUpload();
    await acceptUpload();
    await expect(pending).rejects.toThrow('Protection request returned HTTP 503');
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(MockXHR.instances).toHaveLength(1);
  });
});
