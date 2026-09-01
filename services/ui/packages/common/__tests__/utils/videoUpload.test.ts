// SPDX-License-Identifier: MIT
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
