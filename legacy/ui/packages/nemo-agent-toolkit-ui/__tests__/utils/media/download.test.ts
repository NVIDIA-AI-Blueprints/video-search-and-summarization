jest.mock('file-saver', () => ({
  saveAs: jest.fn(),
}));

import { saveAs } from 'file-saver';

import { downloadImageFromUrl } from '@/utils/media/download';

describe('downloadImageFromUrl', () => {
  afterEach(() => {
    jest.clearAllMocks();
  });

  it('downloads data URLs via blob + saveAs', async () => {
    const dataUrl =
      'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';

    await downloadImageFromUrl(dataUrl, 'Snapshot at 00:05');

    expect(saveAs).toHaveBeenCalledTimes(1);
    const [blob, filename] = (saveAs as jest.Mock).mock.calls[0];
    expect(blob).toBeInstanceOf(Blob);
    expect(filename).toMatch(/^Snapshot at 00_05\./);
  });

  it('rejects loading placeholder src', async () => {
    await expect(downloadImageFromUrl('loading')).rejects.toThrow('not ready');
  });

  it('fetches remote URLs and saves as blob', async () => {
    const blob = new Blob(['pixels'], { type: 'image/jpeg' });
    global.fetch = jest.fn().mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(blob),
    }) as jest.Mock;

    await downloadImageFromUrl('https://example.com/snapshot.jpg', 'snapshot');

    expect(global.fetch).toHaveBeenCalled();
    expect(saveAs).toHaveBeenCalledTimes(1);
    const [outBlob, name] = (saveAs as jest.Mock).mock.calls[0];
    expect(outBlob).toBe(blob);
    expect(name).toBe('snapshot.jpg');
  });

  it('never triggers same-tab navigation when fetch fails', async () => {
    global.fetch = jest.fn().mockRejectedValue(new Error('CORS')) as jest.Mock;

    const imageStub = {
      set crossOrigin(_: string) {
        /* noop */
      },
      set src(_: string) {
        queueMicrotask(() => {
          imageStub.onerror?.(new Event('error'));
        });
      },
      onload: null as null | (() => void),
      onerror: null as null | ((_e: Event) => void),
      naturalWidth: 0,
      naturalHeight: 0,
    };
    jest.spyOn(global, 'Image').mockImplementation(() => imageStub as unknown as HTMLImageElement);

    await expect(downloadImageFromUrl('https://cdn.example.com/no-cors.jpg', 'x')).rejects.toThrow();

    expect(saveAs).not.toHaveBeenCalled();
  });
});
