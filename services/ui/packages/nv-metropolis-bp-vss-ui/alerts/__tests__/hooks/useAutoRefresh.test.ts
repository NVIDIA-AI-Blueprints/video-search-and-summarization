// SPDX-License-Identifier: MIT
import { renderHook, act } from '@testing-library/react';
import { useAutoRefresh } from '../../lib-src/hooks/useAutoRefresh';

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

  it('calls onRefresh at the configured interval', () => {
    const onRefresh = jest.fn();
    renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true })
    );

    expect(onRefresh).not.toHaveBeenCalled();

    jest.advanceTimersByTime(1000);
    expect(onRefresh).toHaveBeenCalledTimes(1);

    jest.advanceTimersByTime(1000);
    expect(onRefresh).toHaveBeenCalledTimes(2);
  });

  it('does not call onRefresh when disabled', () => {
    const onRefresh = jest.fn();
    renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: false })
    );

    jest.advanceTimersByTime(5000);
    expect(onRefresh).not.toHaveBeenCalled();
  });

  it('does not call onRefresh when isActive is false', () => {
    const onRefresh = jest.fn();
    renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true, isActive: false })
    );

    jest.advanceTimersByTime(5000);
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

  it('setInterval updates the interval', () => {
    const onRefresh = jest.fn();
    const { result } = renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true })
    );

    act(() => {
      result.current.setInterval(3000);
    });

    expect(result.current.interval).toBe(3000);
    onRefresh.mockClear();

    jest.advanceTimersByTime(2999);
    expect(onRefresh).not.toHaveBeenCalled();

    jest.advanceTimersByTime(1);
    expect(onRefresh).toHaveBeenCalledTimes(1);
  });

  it('stops calling onRefresh when disabled after being enabled', () => {
    const onRefresh = jest.fn();
    const { result } = renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true })
    );

    jest.advanceTimersByTime(1000);
    expect(onRefresh).toHaveBeenCalledTimes(1);

    act(() => {
      result.current.setIsEnabled(false);
    });

    jest.advanceTimersByTime(5000);
    expect(onRefresh).toHaveBeenCalledTimes(1); // no new calls
  });

  it('cleans up interval on unmount', () => {
    const onRefresh = jest.fn();
    const { unmount } = renderHook(() =>
      useAutoRefresh({ onRefresh, defaultInterval: 1000, enabled: true })
    );

    unmount();

    jest.advanceTimersByTime(5000);
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
});
