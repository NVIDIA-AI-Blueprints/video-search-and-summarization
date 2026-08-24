// SPDX-License-Identifier: MIT
import { copyToClipboard } from '../../lib-src/utils/clipboard';

describe('copyToClipboard', () => {
  let consoleErrorSpy: jest.SpyInstance;
  const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
  const originalIsSecureContext = Object.getOwnPropertyDescriptor(window, 'isSecureContext');
  const originalExecCommand = document.execCommand;

  beforeEach(() => {
    consoleErrorSpy = jest.spyOn(console, 'error').mockImplementation();
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
    if (originalClipboard) {
      Object.defineProperty(navigator, 'clipboard', originalClipboard);
    } else if ('clipboard' in navigator) {
      Object.defineProperty(navigator, 'clipboard', { value: undefined, writable: true, configurable: true });
    }
    if (originalIsSecureContext) {
      Object.defineProperty(window, 'isSecureContext', originalIsSecureContext);
    } else {
      Object.defineProperty(window, 'isSecureContext', { value: false, writable: true, configurable: true });
    }
    document.execCommand = originalExecCommand;
  });

  it('returns true when clipboard API succeeds', async () => {
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    Object.defineProperty(window, 'isSecureContext', {
      value: true,
      writable: true,
      configurable: true,
    });

    const result = await copyToClipboard('test content');

    expect(result).toBe(true);
    expect(writeText).toHaveBeenCalledWith('test content');
  });

  it('falls back to execCommand when clipboard API not available', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      writable: true,
      configurable: true,
    });

    const execCommand = jest.fn().mockReturnValue(true);
    document.execCommand = execCommand;

    const result = await copyToClipboard('fallback content');

    expect(result).toBe(true);
    expect(execCommand).toHaveBeenCalledWith('copy');
  });

  it('returns false when execCommand fails', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      value: undefined,
      writable: true,
      configurable: true,
    });

    document.execCommand = jest.fn().mockReturnValue(false);

    const result = await copyToClipboard('content');

    expect(result).toBe(false);
    expect(consoleErrorSpy).toHaveBeenCalled();
  });

  it('returns false when clipboard API throws', async () => {
    const writeText = jest.fn().mockRejectedValue(new Error('Clipboard error'));
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText },
      writable: true,
      configurable: true,
    });
    Object.defineProperty(window, 'isSecureContext', {
      value: true,
      writable: true,
      configurable: true,
    });

    const result = await copyToClipboard('content');

    expect(result).toBe(false);
    expect(consoleErrorSpy).toHaveBeenCalled();
  });
});
