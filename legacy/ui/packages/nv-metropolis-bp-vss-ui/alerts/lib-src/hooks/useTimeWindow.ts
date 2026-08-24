// SPDX-License-Identifier: MIT
/**
 * Custom React hook for managing time window state and operations
 * 
 * This hook provides comprehensive time window management including state handling,
 * custom time input validation, and user interaction management for the time
 * selection interface.
 */

import { useState, useCallback } from 'react';
import { parseTimeInput, parseTimeLimit, formatTimeWindow } from '../utils/timeUtils';
import { useSessionState } from './useSessionState';

interface UseTimeWindowOptions {
  defaultTimeWindow?: number;
  maxSearchTimeLimit?: string;
}

/**
 * Custom React hook for managing time window selection and validation
 * 
 */
export const useTimeWindow = ({ defaultTimeWindow = 10, maxSearchTimeLimit }: UseTimeWindowOptions = {}) => {
  const [timeWindow, setTimeWindow] = useSessionState<number>(
    'alertsTabTimeWindow', defaultTimeWindow,
    (s) => { const n = Number(s); return Number.isInteger(n) && n > 0 ? n : null; },
  );

  const [showCustomTimeInput, setShowCustomTimeInput] = useState<boolean>(false);
  const [customTimeValue, setCustomTimeValue] = useState<string>('');
  const [customTimeError, setCustomTimeError] = useState<string>('');

  // Parse max time limit (0 means unlimited)
  const maxTimeLimitInMinutes = parseTimeLimit(maxSearchTimeLimit);

  const handleCustomTimeChange = useCallback((value: string) => {
    setCustomTimeValue(value);
    if (value.trim()) {
      const result = parseTimeInput(value);
      if (!result.error && maxTimeLimitInMinutes > 0 && result.minutes > maxTimeLimitInMinutes) {
        setCustomTimeError(`Time cannot exceed ${formatTimeWindow(maxTimeLimitInMinutes)}`);
      } else {
        setCustomTimeError(result.error);
      }
    } else {
      setCustomTimeError('');
    }
  }, [maxTimeLimitInMinutes]);

  const handleSetCustomTime = useCallback(() => {
    if (customTimeError) return;
    const result = parseTimeInput(customTimeValue);
    if (result.minutes > 0 && !result.error) {
      setTimeWindow(result.minutes);
      setShowCustomTimeInput(false);
      setCustomTimeValue('');
      setCustomTimeError('');
    }
  }, [customTimeError, customTimeValue, setTimeWindow]);

  const handleCancelCustomTime = useCallback(() => {
    setShowCustomTimeInput(false);
    setCustomTimeValue('');
    setCustomTimeError('');
  }, []);

  const openCustomTimeInput = useCallback(() => {
    setShowCustomTimeInput(true);
  }, []);

  return {
    timeWindow,
    setTimeWindow,
    showCustomTimeInput,
    customTimeValue,
    customTimeError,
    maxTimeLimitInMinutes,
    handleCustomTimeChange,
    handleSetCustomTime,
    handleCancelCustomTime,
    openCustomTimeInput
  };
};
