import { resolveWebSocketMode } from '@/utils/app/chatTransport';

describe('resolveWebSocketMode', () => {
  it('ignores a saved WebSocket preference when HTTP transport is forced', () => {
    expect(
      resolveWebSocketMode({
        forceHttpTransport: true,
        storedWebSocketMode: 'true',
        configuredWebSocketMode: true,
      }),
    ).toBe(false);
  });

  it('uses the saved preference when transport is not locked', () => {
    expect(
      resolveWebSocketMode({
        forceHttpTransport: false,
        storedWebSocketMode: 'true',
        configuredWebSocketMode: false,
      }),
    ).toBe(true);
  });

  it('falls back to the configured default when no preference exists', () => {
    expect(
      resolveWebSocketMode({
        forceHttpTransport: false,
        storedWebSocketMode: null,
        configuredWebSocketMode: true,
      }),
    ).toBe(true);
  });
});
