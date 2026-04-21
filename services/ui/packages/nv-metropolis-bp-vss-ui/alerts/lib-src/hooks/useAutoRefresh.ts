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
  onRefresh: () => void;
  enabled?: boolean; // default enabled state
  isActive?: boolean; // whether the component is currently active/visible
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
  // Load initial state from sessionStorage or use defaults
  const [isEnabled, setIsEnabled] = useState<boolean>(() => 
    loadFromStorage(STORAGE_KEY_ENABLED, enabled)
  );
  const [intervalValue, setIntervalValue] = useState<number>(() => 
    loadFromStorage(STORAGE_KEY_INTERVAL, defaultInterval)
  );
  const intervalIdRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const onRefreshRef = useRef(onRefresh);

  // Keep onRefresh ref up to date
  useEffect(() => {
    onRefreshRef.current = onRefresh;
  }, [onRefresh]);

  // Clear existing interval when settings change or component unmounts
  // Also pause when isActive is false (tab is hidden)
  useEffect(() => {
    // Clear any existing interval
    if (intervalIdRef.current) {
      clearInterval(intervalIdRef.current);
      intervalIdRef.current = null;
    }

    // Set up new interval if enabled AND active (visible)
    if (isEnabled && isActive && intervalValue > 0) {
      intervalIdRef.current = setInterval(() => {
        onRefreshRef.current();
      }, intervalValue);
    }

    // Cleanup function
    return () => {
      if (intervalIdRef.current) {
        clearInterval(intervalIdRef.current);
        intervalIdRef.current = null;
      }
    };
  }, [isEnabled, intervalValue, isActive]);

  // Save to sessionStorage whenever values change
  useEffect(() => {
    saveToStorage(STORAGE_KEY_ENABLED, isEnabled);
  }, [isEnabled]);

  useEffect(() => {
    saveToStorage(STORAGE_KEY_INTERVAL, intervalValue);
  }, [intervalValue]);

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

