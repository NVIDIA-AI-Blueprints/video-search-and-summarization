// SPDX-License-Identifier: MIT
/**
 * Custom React hook for managing time window state and operations
 * 
 * This hook provides comprehensive time window management including state handling,
 * custom time input validation, and user interaction management for the time
 * selection interface.
 */

import { useState } from 'react';
import { parseTimeInput, parseTimeLimit, formatTimeWindow } from '../utils/timeUtils';

interface UseTimeWindowOptions {
  defaultTimeWindow?: number;
  maxSearchTimeLimit?: string;
}

/**
 * Custom React hook for managing time window selection and validation
 * 
 */
export const useTimeWindow = ({ defaultTimeWindow = 10, maxSearchTimeLimit }: UseTimeWindowOptions = {}) => {
  const [timeWindow, setTimeWindow] = useState<number>(defaultTimeWindow);
  const [showCustomTimeInput, setShowCustomTimeInput] = useState<boolean>(false);
  const [customTimeValue, setCustomTimeValue] = useState<string>('');
  const [customTimeError, setCustomTimeError] = useState<string>('');

  // Parse max time limit (0 means unlimited)
  const maxTimeLimitInMinutes = parseTimeLimit(maxSearchTimeLimit);

  /**
   * Handles changes to the custom time input field with real-time validation
   * 
   */
  const handleCustomTimeChange = (value: string) => {
    setCustomTimeValue(value);
    if (value.trim()) {
      const result = parseTimeInput(value);
      
      // Check if input exceeds max time limit (if limit is set)
      if (!result.error && maxTimeLimitInMinutes > 0 && result.minutes > maxTimeLimitInMinutes) {
        setCustomTimeError(`Time cannot exceed ${formatTimeWindow(maxTimeLimitInMinutes)}`);
      } else {
        setCustomTimeError(result.error);
      }
    } else {
      setCustomTimeError('');
    }
  };

  /**
   * Applies the custom time input if validation passes
   */
  const handleSetCustomTime = () => {
    // Don't apply if there's already an error in state
    if (customTimeError) {
      return;
    }
    
    const result = parseTimeInput(customTimeValue);
    
    // Apply if valid (validation already done in handleCustomTimeChange)
    if (result.minutes > 0 && !result.error) {
      setTimeWindow(result.minutes);
      setShowCustomTimeInput(false);
      setCustomTimeValue('');
      setCustomTimeError('');
    }
  };

  /**
   * Cancels custom time input and resets the modal state
   * 
   */
  const handleCancelCustomTime = () => {
    setShowCustomTimeInput(false);
    setCustomTimeValue('');
    setCustomTimeError('');
  };

  /**
   * Opens the custom time input modal
   * 
   */
  const openCustomTimeInput = () => {
    setShowCustomTimeInput(true);
  };

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
