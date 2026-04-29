// SPDX-License-Identifier: MIT
/**
 * Custom React hook for managing auto-refresh functionality
 * 
 * This hook provides auto-refresh capabilities with configurable interval in milliseconds,
 * enable/disable controls, and automatic cleanup. The interval and enabled state are
 * persisted in sessionStorage, so they persist across component switches but reset
 * when the page is refreshed or the browser tab is closed.
 */

import { useState, useEffect, useRef } from 'react';

/**
 * Configuration options for the useAutoRefresh hook
 */
interface UseAutoRefreshOptions {
  defaultInterval?: number; // in milliseconds
  onRefresh: () => Promise<boolean> | Promise<void> | void;
  enabled?: boolean; // default enabled state
  /** When false, refresh timers are not scheduled (e.g. tab hidden or alerts table not on page 1). */
  isActive?: boolean;
}

/**
 * Return type for the useAutoRefresh hook
 */
interface UseAutoRefreshReturn {
  isEnabled: boolean;
  interval: number; // in milliseconds
  setIsEnabled: (enabled: boolean) => void;
  setInterval: (milliseconds: number) => void;
  toggleEnabled: () => void;
}

// Storage keys for persistence
const STORAGE_KEY_INTERVAL = 'alertAutoRefreshInterval';
const STORAGE_KEY_ENABLED = 'alertAutoRefreshEnabled';

/**
 * Load value from sessionStorage with fallback to default
 */
const loadFromStorage = <T,>(key: string, defaultValue: T): T => {
  if (typeof window === 'undefined') return defaultValue;
  
  try {
    const item = sessionStorage.getItem(key);
    return item ? JSON.parse(item) : defaultValue;
  } catch (error) {
    console.warn(`Failed to load ${key} from sessionStorage:`, error);
    return defaultValue;
  }
};

/**
 * Save value to sessionStorage
 */
const saveToStorage = <T,>(key: string, value: T): void => {
  if (typeof window === 'undefined') return;
  
  try {
    sessionStorage.setItem(key, JSON.stringify(value));
  } catch (error) {
    console.warn(`Failed to save ${key} to sessionStorage:`, error);
  }
};

/**
 * Custom React hook for managing auto-refresh functionality
 * 
 * @param options - Configuration options for auto-refresh
 * @returns Auto-refresh state and control functions
 */
export const useAutoRefresh = ({
  defaultInterval = 1000,
  onRefresh,
  enabled = true,
  isActive = true
}: UseAutoRefreshOptions): UseAutoRefreshReturn => {
  // Initialize with the SSR-safe defaults so server / client first render
  // match. The persisted values are pulled from sessionStorage in a one-shot
  // effect after mount (see below).
  const [isEnabled, setIsEnabled] = useState<boolean>(enabled);
  const [intervalValue, setIntervalValue] = useState<number>(defaultInterval);
  // `hydrated` must be state (not a ref) so flipping it triggers a re-render
  // *after* the hydration effect's setState calls have been applied. The save
  // effects below then run with the actual stored values rather than the
  // still-stale defaults — otherwise they'd write the defaults back to
  // storage on mount and permanently clobber the user's settings if the
  // component unmounted before the corrective re-render.
  const [hydrated, setHydrated] = useState(false);
  const timeoutIdRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const onRefreshRef = useRef(onRefresh);

  useEffect(() => {
    onRefreshRef.current = onRefresh;
  }, [onRefresh]);

  useEffect(() => {
    setIsEnabled(loadFromStorage(STORAGE_KEY_ENABLED, enabled));
    setIntervalValue(loadFromStorage(STORAGE_KEY_INTERVAL, defaultInterval));
    setHydrated(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Uses setTimeout-based scheduling so the next refresh only fires after
  // the previous onRefresh call completes (success or failure).
  // This prevents overlapping API calls when a request takes longer than the interval.
  useEffect(() => {
    if (timeoutIdRef.current) {
      clearTimeout(timeoutIdRef.current);
      timeoutIdRef.current = null;
    }

    if (!isEnabled || !isActive || intervalValue <= 0) return;

    let cancelled = false;

    const scheduleNext = () => {
      if (cancelled) return;

      timeoutIdRef.current = setTimeout(async () => {
        if (cancelled) return;

        try {
          await Promise.resolve(onRefreshRef.current());
        } catch {
          // Ignore errors — continue the chain regardless
        }

        if (!cancelled) {
          scheduleNext();
        }
      }, intervalValue);
    };

    scheduleNext();

    return () => {
      cancelled = true;
      if (timeoutIdRef.current) {
        clearTimeout(timeoutIdRef.current);
        timeoutIdRef.current = null;
      }
    };
  }, [isEnabled, intervalValue, isActive]);

  // Save to sessionStorage whenever values change — gated on `hydrated` so
  // we don't overwrite the stored value with the SSR default on mount.
  useEffect(() => {
    if (!hydrated) return;
    saveToStorage(STORAGE_KEY_ENABLED, isEnabled);
  }, [isEnabled, hydrated]);

  useEffect(() => {
    if (!hydrated) return;
    saveToStorage(STORAGE_KEY_INTERVAL, intervalValue);
  }, [intervalValue, hydrated]);

  const toggleEnabled = () => {
    setIsEnabled(prev => !prev);
  };

  return {
    isEnabled,
    interval: intervalValue,
    setIsEnabled,
    setInterval: setIntervalValue,
    toggleEnabled
  };
};

