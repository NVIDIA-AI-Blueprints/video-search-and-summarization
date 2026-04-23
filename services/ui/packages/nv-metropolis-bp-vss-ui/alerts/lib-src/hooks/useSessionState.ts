// SPDX-License-Identifier: MIT
import { useState, useCallback, type Dispatch, type SetStateAction } from 'react';

/**
 * useState that persists to sessionStorage.
 *
 * Reads from sessionStorage synchronously in the useState initializer so the
 * stored value is available from the very first render — no flash of defaults,
 * no extra API calls with stale params.
 *
 * Writes happen synchronously inside the returned setter so there is no
 * useEffect race condition where a write-effect could overwrite the stored
 * value with the default before the read-effect's state update commits.
 *
 * On the server (`typeof window === 'undefined'`) the initializer returns
 * `defaultValue`, which may cause a React hydration mismatch on the client.
 * React 18 handles this gracefully by patching the DOM to the client value.
 *
 * @param key           sessionStorage key
 * @param defaultValue  value used when nothing is stored (and on the server)
 * @param parse         convert the stored string → T, or return null to reject
 */
export function useSessionState<T extends string | number | boolean>(
  key: string,
  defaultValue: T,
  parse: (stored: string) => T | null,
): [T, Dispatch<SetStateAction<T>>] {
  const [value, _setValue] = useState<T>(() => {
    if (typeof window === 'undefined') return defaultValue;
    try {
      const stored = sessionStorage.getItem(key);
      if (stored != null) {
        const parsed = parse(stored);
        if (parsed != null) return parsed;
      }
    } catch { /* ignore */ }
    return defaultValue;
  });

  const setValue: Dispatch<SetStateAction<T>> = useCallback((action) => {
    _setValue((prev) => {
      const next = typeof action === 'function' ? (action as (prev: T) => T)(prev) : action;
      try { sessionStorage.setItem(key, String(next)); } catch { /* ignore */ }
      return next;
    });
  }, [key]);

  return [value, setValue];
}

export const parseIntRange = (min: number, max: number) =>
  (s: string): number | null => {
    const n = Number(s);
    return Number.isInteger(n) && n >= min && n <= max ? n : null;
  };
