// SPDX-License-Identifier: MIT
import { renderHook, act } from '@testing-library/react';
import { useTheme } from '../../hooks/useTheme';
import { setMockEnv, clearMockEnv } from 'next-runtime-env';

const sessionStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: jest.fn((key: string) => store[key] ?? null),
    setItem: jest.fn((key: string, value: string) => { store[key] = value; }),
    removeItem: jest.fn((key: string) => { delete store[key]; }),
    clear: jest.fn(() => { store = {}; }),
  };
})();

Object.defineProperty(window, 'sessionStorage', { value: sessionStorageMock, configurable: true });

beforeEach(() => {
  clearMockEnv();
  sessionStorageMock.clear();
  jest.clearAllMocks();
  document.documentElement.classList.remove('dark');
});

describe('useTheme', () => {
  it('defaults to dark theme when no env var is set', () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('dark');
    expect(result.current.isDark).toBe(true);
    expect(result.current.isLight).toBe(false);
  });

  it('defaults to light theme when NEXT_PUBLIC_DARK_THEME_DEFAULT is "false"', () => {
    setMockEnv('NEXT_PUBLIC_DARK_THEME_DEFAULT', 'false');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('light');
    expect(result.current.isLight).toBe(true);
  });

  it('toggleTheme switches from dark to light', () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.isDark).toBe(true);

    act(() => {
      result.current.toggleTheme();
    });

    expect(result.current.theme).toBe('light');
    expect(result.current.isLight).toBe(true);
  });

  it('toggleTheme switches from light to dark', () => {
    setMockEnv('NEXT_PUBLIC_DARK_THEME_DEFAULT', 'false');
    const { result } = renderHook(() => useTheme());
    expect(result.current.isLight).toBe(true);

    act(() => {
      result.current.toggleTheme();
    });

    expect(result.current.theme).toBe('dark');
    expect(result.current.isDark).toBe(true);
  });

  it('setTheme directly sets the theme', () => {
    const { result } = renderHook(() => useTheme());

    act(() => {
      result.current.setTheme('light');
    });
    expect(result.current.theme).toBe('light');

    act(() => {
      result.current.setTheme('dark');
    });
    expect(result.current.theme).toBe('dark');
  });

  it('restores saved theme from sessionStorage', () => {
    sessionStorageMock.getItem.mockReturnValue('light');
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe('light');
  });

  it('saves theme to sessionStorage on change', () => {
    const { result } = renderHook(() => useTheme());

    act(() => {
      result.current.toggleTheme();
    });

    expect(sessionStorageMock.setItem).toHaveBeenCalledWith('lightMode', 'light');
  });
});
