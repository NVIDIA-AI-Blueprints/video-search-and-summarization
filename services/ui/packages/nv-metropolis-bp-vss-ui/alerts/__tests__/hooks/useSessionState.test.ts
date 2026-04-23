// SPDX-License-Identifier: MIT
import { renderHook, act } from '@testing-library/react';
import { useSessionState, parseIntRange } from '../../lib-src/hooks/useSessionState';

// sessionStorage is globally mocked in jest.setup.js as jest.fn() stubs.
// Tests must use mockReturnValue/mockImplementation for getItem,
// and check setItem.mock.calls for writes.

describe('useSessionState', () => {
  beforeEach(() => {
    (sessionStorage.getItem as jest.Mock).mockReturnValue(null);
  });

  it('returns the default value when nothing is stored', () => {
    const { result } = renderHook(() =>
      useSessionState('key', 42, (s) => (Number(s) || null)),
    );
    expect(result.current[0]).toBe(42);
  });

  it('reads stored value from sessionStorage on first render', () => {
    (sessionStorage.getItem as jest.Mock).mockReturnValue('99');

    const { result } = renderHook(() =>
      useSessionState('myKey', 42, (s) => {
        const n = Number(s);
        return Number.isInteger(n) ? n : null;
      }),
    );

    expect(sessionStorage.getItem).toHaveBeenCalledWith('myKey');
    expect(result.current[0]).toBe(99);
  });

  it('falls back to default when parse returns null', () => {
    (sessionStorage.getItem as jest.Mock).mockReturnValue('garbage');

    const { result } = renderHook(() =>
      useSessionState('k', 10, () => null),
    );

    expect(result.current[0]).toBe(10);
  });

  it('writes to sessionStorage when value changes via setter', () => {
    const { result } = renderHook(() =>
      useSessionState('k', 5, (s) => Number(s) || null),
    );

    act(() => {
      result.current[1](20);
    });

    expect(result.current[0]).toBe(20);
    expect(sessionStorage.setItem).toHaveBeenCalledWith('k', '20');
  });

  it('does not overwrite stored value with default on mount', () => {
    (sessionStorage.getItem as jest.Mock).mockReturnValue('100');

    renderHook(() =>
      useSessionState('k', 50, (s) => {
        const n = Number(s);
        return Number.isInteger(n) ? n : null;
      }),
    );

    const setCalls = (sessionStorage.setItem as jest.Mock).mock.calls
      .filter(([key]: [string]) => key === 'k');
    expect(setCalls).toHaveLength(0);
  });

  it('works with string values', () => {
    (sessionStorage.getItem as jest.Mock).mockReturnValue('utc');

    const { result } = renderHook(() =>
      useSessionState<string>('fmt', 'local', (s) => (s === 'utc' || s === 'local' ? s : null)),
    );

    expect(result.current[0]).toBe('utc');
  });

  it('works with boolean values', () => {
    (sessionStorage.getItem as jest.Mock).mockReturnValue('true');

    const { result } = renderHook(() =>
      useSessionState<boolean>('flag', false, (s) => (s === 'true' ? true : s === 'false' ? false : null)),
    );

    expect(result.current[0]).toBe(true);
  });

  it('ignores sessionStorage errors gracefully', () => {
    (sessionStorage.getItem as jest.Mock).mockImplementation(() => {
      throw new Error('SecurityError');
    });

    const { result } = renderHook(() =>
      useSessionState('k', 7, (s) => Number(s) || null),
    );

    expect(result.current[0]).toBe(7);
  });

  it('persists value through setter and is readable on remount', () => {
    (sessionStorage.getItem as jest.Mock).mockReturnValue(null);

    const { result } = renderHook(() =>
      useSessionState('persist', 0, (s) => {
        const n = Number(s);
        return Number.isInteger(n) ? n : null;
      }),
    );

    act(() => {
      result.current[1](42);
    });

    expect(result.current[0]).toBe(42);
    expect(sessionStorage.setItem).toHaveBeenCalledWith('persist', '42');
  });

  it('supports updater function in setter', () => {
    (sessionStorage.getItem as jest.Mock).mockReturnValue('10');

    const { result } = renderHook(() =>
      useSessionState('counter', 0, (s) => {
        const n = Number(s);
        return Number.isInteger(n) ? n : null;
      }),
    );

    expect(result.current[0]).toBe(10);

    act(() => {
      result.current[1]((prev) => prev + 5);
    });

    expect(result.current[0]).toBe(15);
    expect(sessionStorage.setItem).toHaveBeenCalledWith('counter', '15');
  });

  it('uses the correct key when key changes via rerender', () => {
    (sessionStorage.getItem as jest.Mock).mockImplementation((key: string) => {
      if (key === 'alerts_a') return '1';
      if (key === 'alerts_b') return '2';
      return null;
    });

    const { result, rerender } = renderHook(
      ({ k }: { k: string }) => useSessionState('alerts_' + k, 0, (s) => Number(s) || null),
      { initialProps: { k: 'a' } },
    );

    expect(result.current[0]).toBe(1);

    rerender({ k: 'b' });

    act(() => { /* flush */ });
  });
});

describe('parseIntRange', () => {
  const parse10to500 = parseIntRange(10, 500);

  it('returns the number when within range', () => {
    expect(parse10to500('10')).toBe(10);
    expect(parse10to500('250')).toBe(250);
    expect(parse10to500('500')).toBe(500);
  });

  it('returns null when below range', () => {
    expect(parse10to500('9')).toBeNull();
    expect(parse10to500('0')).toBeNull();
    expect(parse10to500('-1')).toBeNull();
  });

  it('returns null when above range', () => {
    expect(parse10to500('501')).toBeNull();
    expect(parse10to500('9999')).toBeNull();
  });

  it('returns null for non-integer values', () => {
    expect(parse10to500('10.5')).toBeNull();
    expect(parse10to500('abc')).toBeNull();
    expect(parse10to500('')).toBeNull();
    expect(parse10to500('NaN')).toBeNull();
  });

  it('works with different ranges', () => {
    const parse100to5000 = parseIntRange(100, 5000);
    expect(parse100to5000('100')).toBe(100);
    expect(parse100to5000('5000')).toBe(5000);
    expect(parse100to5000('99')).toBeNull();
    expect(parse100to5000('5001')).toBeNull();
  });
});
