// SPDX-License-Identifier: MIT
import { renderHook, act } from '@testing-library/react';
import { useTimeWindow } from '../../lib-src/hooks/useTimeWindow';

describe('useTimeWindow', () => {
  it('initializes with default time window', () => {
    const { result } = renderHook(() => useTimeWindow());
    expect(result.current.timeWindow).toBe(10);
    expect(result.current.showCustomTimeInput).toBe(false);
    expect(result.current.customTimeValue).toBe('');
    expect(result.current.customTimeError).toBe('');
  });

  it('initializes with custom default time window', () => {
    const { result } = renderHook(() => useTimeWindow({ defaultTimeWindow: 60 }));
    expect(result.current.timeWindow).toBe(60);
  });

  it('setTimeWindow updates the time window', () => {
    const { result } = renderHook(() => useTimeWindow());

    act(() => {
      result.current.setTimeWindow(120);
    });

    expect(result.current.timeWindow).toBe(120);
  });

  it('openCustomTimeInput shows the custom input modal', () => {
    const { result } = renderHook(() => useTimeWindow());

    act(() => {
      result.current.openCustomTimeInput();
    });

    expect(result.current.showCustomTimeInput).toBe(true);
  });

  it('handleCancelCustomTime resets modal state', () => {
    const { result } = renderHook(() => useTimeWindow());

    act(() => {
      result.current.openCustomTimeInput();
      result.current.handleCustomTimeChange('2h');
    });

    act(() => {
      result.current.handleCancelCustomTime();
    });

    expect(result.current.showCustomTimeInput).toBe(false);
    expect(result.current.customTimeValue).toBe('');
    expect(result.current.customTimeError).toBe('');
  });

  describe('handleCustomTimeChange', () => {
    it('validates and clears error for valid input', () => {
      const { result } = renderHook(() => useTimeWindow());

      act(() => {
        result.current.handleCustomTimeChange('2h');
      });

      expect(result.current.customTimeValue).toBe('2h');
      expect(result.current.customTimeError).toBe('');
    });

    it('sets error for invalid input', () => {
      const { result } = renderHook(() => useTimeWindow());

      act(() => {
        result.current.handleCustomTimeChange('invalid');
      });

      expect(result.current.customTimeValue).toBe('invalid');
      expect(result.current.customTimeError).toBeTruthy();
    });

    it('clears error when input is emptied', () => {
      const { result } = renderHook(() => useTimeWindow());

      act(() => {
        result.current.handleCustomTimeChange('invalid');
      });
      expect(result.current.customTimeError).toBeTruthy();

      act(() => {
        result.current.handleCustomTimeChange('');
      });
      expect(result.current.customTimeError).toBe('');
    });

    it('sets error when exceeding max time limit', () => {
      const { result } = renderHook(() =>
        useTimeWindow({ maxSearchTimeLimit: '1h' }) // 60 minutes
      );

      act(() => {
        result.current.handleCustomTimeChange('2h'); // 120 minutes > 60
      });

      expect(result.current.customTimeError).toContain('cannot exceed');
    });

    it('allows values within max time limit', () => {
      const { result } = renderHook(() =>
        useTimeWindow({ maxSearchTimeLimit: '2h' })
      );

      act(() => {
        result.current.handleCustomTimeChange('1h');
      });

      expect(result.current.customTimeError).toBe('');
    });
  });

  describe('handleSetCustomTime', () => {
    it('applies valid custom time and closes modal', () => {
      const { result } = renderHook(() => useTimeWindow());

      act(() => {
        result.current.openCustomTimeInput();
        result.current.handleCustomTimeChange('45m');
      });

      act(() => {
        result.current.handleSetCustomTime();
      });

      expect(result.current.timeWindow).toBe(45);
      expect(result.current.showCustomTimeInput).toBe(false);
      expect(result.current.customTimeValue).toBe('');
    });

    it('does not apply when there is an error', () => {
      const { result } = renderHook(() => useTimeWindow());

      act(() => {
        result.current.openCustomTimeInput();
        result.current.handleCustomTimeChange('invalid');
      });

      act(() => {
        result.current.handleSetCustomTime();
      });

      expect(result.current.timeWindow).toBe(10); // unchanged
      expect(result.current.showCustomTimeInput).toBe(true); // still open
    });

    it('does not apply empty value', () => {
      const { result } = renderHook(() => useTimeWindow());

      act(() => {
        result.current.openCustomTimeInput();
      });

      act(() => {
        result.current.handleSetCustomTime();
      });

      expect(result.current.timeWindow).toBe(10); // unchanged
    });
  });

  it('parses maxSearchTimeLimit correctly', () => {
    const { result } = renderHook(() =>
      useTimeWindow({ maxSearchTimeLimit: '1d' })
    );

    expect(result.current.maxTimeLimitInMinutes).toBe(1440);
  });

  it('returns 0 for unlimited max time limit', () => {
    const { result } = renderHook(() =>
      useTimeWindow({ maxSearchTimeLimit: '0' })
    );

    expect(result.current.maxTimeLimitInMinutes).toBe(0);
  });
});
