// SPDX-License-Identifier: MIT
import { useState, useCallback, type Dispatch, type SetStateAction } from 'react';
import { FilterState } from '../types';
import { createEmptyFilterState } from './useFilters';

/**
 * useState for `FilterState` that persists to sessionStorage.
 *
 * Reads from sessionStorage synchronously in the useState initializer so
 * filters are available on the first render (no flash-of-empty-filters and
 * no extra API call with unfiltered params on page load/refresh).
 *
 * Writes happen inside the setter so changes propagate to storage immediately,
 * and no-op updates (when the updater returns the same reference) skip the
 * serialization/write entirely.
 *
 * @param key sessionStorage key
 */
export function useSessionFilterState(
  key: string,
): [FilterState, Dispatch<SetStateAction<FilterState>>] {
  const [value, _setValue] = useState<FilterState>(() => {
    if (typeof window === 'undefined') return createEmptyFilterState();
    try {
      const stored = sessionStorage.getItem(key);
      if (stored) {
        const parsed = JSON.parse(stored);
        if (parsed && typeof parsed === 'object') {
          return {
            sensors: new Set<string>(Array.isArray(parsed.sensors) ? parsed.sensors : []),
            alertTypes: new Set<string>(Array.isArray(parsed.alertTypes) ? parsed.alertTypes : []),
            alertTriggered: new Set<string>(Array.isArray(parsed.alertTriggered) ? parsed.alertTriggered : []),
          };
        }
      }
    } catch { /* ignore */ }
    return createEmptyFilterState();
  });

  const setValue: Dispatch<SetStateAction<FilterState>> = useCallback((action) => {
    _setValue((prev) => {
      const next = typeof action === 'function' ? (action as (prev: FilterState) => FilterState)(prev) : action;
      if (next === prev) return prev;
      try {
        sessionStorage.setItem(key, JSON.stringify({
          sensors: [...next.sensors],
          alertTypes: [...next.alertTypes],
          alertTriggered: [...next.alertTriggered],
        }));
      } catch { /* ignore */ }
      return next;
    });
  }, [key]);

  return [value, setValue];
}
