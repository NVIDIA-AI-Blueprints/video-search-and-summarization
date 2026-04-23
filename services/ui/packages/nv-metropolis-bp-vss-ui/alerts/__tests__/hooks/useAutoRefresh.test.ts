// SPDX-License-Identifier: MIT
import { renderHook, act } from '@testing-library/react';
import { useAutoRefresh } from '../../lib-src/hooks/useAutoRefresh';

/**
 * Advance fake timers and flush the microtask queue so async setTimeout
 * callbacks (Promise-based) are fully resolved before assertions.
 */
async function advanceAndFlush(milliseconds: number) {
  await act(async () => {
    await jest.advanceTimersByTimeAsync(milliseconds);
  });
}

describe('useAutoRefresh', () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    jest.useRealTimers();
  });

  it('initializes with default values', () => {
    const onRefresh = jest.fn();
    const { result } = renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 5000 })
    );

    expect(result.current.isEnabled).toBe(true);
    expect(result.current.interval).toBe(5000);
  });

  it('initializes with enabled=false when specified', () => {
    const onRefresh = jest.fn();
    const { result } = renderHook(() =>
      useAutoRefresh({ onRefresh, enabled: false })
    );

    expect(result.current.isEnabled).toBe(false);
  });

  it('calls onRefresh at the configured interval', async () => {
    const onRefresh = jest.fn().mockResolvedValue(true);
    renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true })
    );

    expect(onRefresh).not.toHaveBeenCalled();

    await advanceAndFlush(1000);
    expect(onRefresh).toHaveBeenCalledTimes(1);

    await advanceAndFlush(1000);
    expect(onRefresh).toHaveBeenCalledTimes(2);
  });

  it('does not call onRefresh when disabled', async () => {
    const onRefresh = jest.fn();
    renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: false })
    );

    await advanceAndFlush(5000);
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('does not call onRefresh when isActive is false', async () => {
    const onRefresh = jest.fn();
    renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true, isActive: false })
    );

    await advanceAndFlush(5000);
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('toggleEnabled flips the enabled state', () => {
    const onRefresh = jest.fn();
    const { result } = renderHook(() =>
      useAutoRefresh({ onRefresh, enabled: true })
    );

    expect(result.current.isEnabled).toBe(true);

    act(() => {
      result.current.toggleEnabled();
    });
    expect(result.current.isEnabled).toBe(false);

    act(() => {
      result.current.toggleEnabled();
    });
    expect(result.current.isEnabled).toBe(true);
  });

  it('setInterval updates the interval', async () => {
    const onRefresh = jest.fn().mockResolvedValue(true);
    const { result } = renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true })
    );

    act(() => {
      result.current.setInterval(3000);
    });

    expect(result.current.interval).toBe(3000);
    onRefresh.mockClear();

    await advanceAndFlush(2999);
    expect(onRefresh).not.toHaveBeenCalled();

    await advanceAndFlush(1);
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('stops calling onRefresh when disabled after being enabled', async () => {
    const onRefresh = jest.fn().mockResolvedValue(true);
    const { result } = renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true })
    );

    await advanceAndFlush(1000);
    expect(onRefresh).toHaveBeenCalledTimes(1);

    act(() => {
      result.current.setIsEnabled(false);
    });

    await advanceAndFlush(5000);
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('cleans up timeout on unmount', async () => {
    const onRefresh = jest.fn().mockResolvedValue(true);
    const { unmount } = renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true })
    );

    unmount();

    await advanceAndFlush(5000);
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('persists enabled state to sessionStorage', () => {
    const onRefresh = jest.fn();
    const { result } = renderHook(() =>
      useAutoRefresh({ onRefresh, enabled: true })
    );

    act(() => {
      result.current.setIsEnabled(false);
    });

    expect(window.sessionStorage.setItem).toHaveBeenCalledWith(
      'alertAutoRefreshEnabled',
      'false'
    );
  });

  it('persists interval to sessionStorage', () => {
    const onRefresh = jest.fn();
    const { result } = renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000 })
    );

    act(() => {
      result.current.setInterval(5000);
    });

    expect(window.sessionStorage.setItem).toHaveBeenCalledWith(
      'alertAutoRefreshInterval',
      '5000'
    );
  });

  it('waits for previous onRefresh to complete before scheduling next', async () => {
    let resolveRefresh!: (value: boolean) => void;
    const onRefresh = jest.fn().mockImplementation(
      () => new Promise<boolean>((resolve) => { resolveRefresh = resolve; })
    );

    renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true })
    );

    // First timeout fires, onRefresh called but pending
    await advanceAndFlush(1000);
    expect(onRefresh).toHaveBeenCalledTimes(1);

    // Second interval elapses but previous call is still pending — no new call
    await advanceAndFlush(1000);
    expect(onRefresh).toHaveBeenCalledTimes(1);

    // Resolve the pending call — scheduleNext will now queue the next timeout
    await act(async () => {
      resolveRefresh(true);
      await jest.advanceTimersByTimeAsync(0);
    });

    // Next interval fires
    await advanceAndFlush(1000);
    expect(onRefresh).toHaveBeenCalledTimes(2);
  });

  it('continues auto-refresh chain when onRefresh returns false', async () => {
    const onRefresh = jest.fn()
      .mockResolvedValueOnce(false)
      .mockResolvedValueOnce(true);

    renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true })
    );

    await advanceAndFlush(1000);
    expect(onRefresh).toHaveBeenCalledTimes(1);

    await advanceAndFlush(1000);
    expect(onRefresh).toHaveBeenCalledTimes(2);
  });

  it('continues auto-refresh chain when onRefresh throws', async () => {
    const onRefresh = jest.fn()
      .mockRejectedValueOnce(new Error('Network error'))
      .mockResolvedValueOnce(true);

    renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true })
    );

    await advanceAndFlush(1000);
    expect(onRefresh).toHaveBeenCalledTimes(1);

    await advanceAndFlush(1000);
    expect(onRefresh).toHaveBeenCalledTimes(2);
  });
});
