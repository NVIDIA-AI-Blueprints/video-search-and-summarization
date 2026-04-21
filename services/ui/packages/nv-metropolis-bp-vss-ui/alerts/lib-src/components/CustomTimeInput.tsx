// SPDX-License-Identifier: MIT
/**
 * CustomTimeInput Component - Advanced Time Selection Modal Interface
 * 
 * This file contains the CustomTimeInput component which provides a sophisticated modal
 * interface for custom time window selection in the alerts management system. The component
 * offers a professional, user-friendly interface for entering custom time durations with
 * comprehensive validation, real-time feedback, and intelligent user interaction handling.
 * 
 * **Key Features:**
 * - Modal-based interface with professional styling and animations
 * - Flexible time input format support (minutes, hours, combined formats)
 * - Real-time validation with immediate user feedback and error messaging
 * - Auto-focus functionality for enhanced user experience
 * - Smart click-outside and keyboard interaction handling (Escape key support)
 * - Comprehensive theme support for both light and dark modes
 * - Accessibility features including proper ARIA labels and keyboard navigation
 * - Format guidance and examples for user education
 * 
 * **Input Format Support:**
 * - Minutes only: "40m", "15m", "120m"
 * - Hours only: "2h", "4h", "24h"
 * - Combined format: "1h 30m", "2h 15m", "3h 45m"
 * - Flexible parsing with case-insensitive input handling
 * - Comprehensive validation with detailed error messages
 */

import React, { useRef, useEffect } from 'react';
import { formatTimeWindow, parseTimeInput } from '../utils/timeUtils';

interface CustomTimeInputProps {
  isOpen: boolean;
  timeWindow: number;
  customTimeValue: string;
  customTimeError: string;
  isDark: boolean;
  maxTimeLimitInMinutes?: number;
  onTimeValueChange: (value: string) => void;
  onApply: () => void;
  onCancel: () => void;
}

export const CustomTimeInput: React.FC<CustomTimeInputProps> = ({
  isOpen,
  timeWindow,
  customTimeValue,
  customTimeError,
  isDark,
  maxTimeLimitInMinutes,
  onTimeValueChange,
  onApply,
  onCancel
}) => {
  const customInputRef = useRef<HTMLInputElement>(null);
  const customContainerRef = useRef<HTMLDivElement>(null);

  // Auto-focus when opened
  useEffect(() => {
    if (isOpen && customInputRef.current) {
      customInputRef.current.focus();
    }
  }, [isOpen]);

  // Handle click outside and escape key
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (customContainerRef.current && !customContainerRef.current.contains(event.target as Node)) {
        if (isOpen) {
          // Auto-apply if valid, otherwise cancel
          const result = parseTimeInput(customTimeValue);
          if (result.minutes > 0 && !result.error) {
            onApply();
          } else {
            onCancel();
          }
        }
      }
    };

    const handleEscapeKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && isOpen) {
        onCancel();
      }
    };

    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      document.addEventListener('keydown', handleEscapeKey);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscapeKey);
    };
  }, [isOpen, customTimeValue, onApply, onCancel]);

  const handleKeyPress = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      // Only apply if there's no error and value is not empty
      if (!customTimeError && customTimeValue.trim()) {
        onApply();
      }
    }
  };

  if (!isOpen) return null;

  return (
    <div 
      ref={customContainerRef} 
      className={`absolute top-full left-0 mt-2 w-80 rounded-lg shadow-lg border z-50 ${
        isDark 
          ? 'bg-gray-800 border-gray-600' 
          : 'bg-white border-gray-200'
      }`}
    >
      {/* Header */}
      <div className={`px-4 py-3 border-b flex items-center justify-between ${
        isDark ? 'border-gray-600' : 'border-gray-200'
      }`}>
        <div className="flex items-center gap-2">
          <div className={`w-6 h-6 rounded flex items-center justify-center ${
            isDark ? 'bg-gray-700' : 'bg-gray-100'
          }`}>
            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M6 2a1 1 0 00-1 1v1H4a2 2 0 00-2 2v10a2 2 0 002 2h12a2 2 0 002-2V6a2 2 0 00-2-2h-1V3a1 1 0 10-2 0v1H7V3a1 1 0 00-1-1zm0 5a1 1 0 000 2h8a1 1 0 100-2H6z" clipRule="evenodd" />
            </svg>
          </div>
          <span className={`text-sm ${isDark ? 'text-gray-300' : 'text-gray-600'}`}>
            ~ {formatTimeWindow(timeWindow)} ago → now
          </span>
        </div>
        <button
          onClick={onCancel}
          className={`text-sm px-3 py-1 rounded ${
            isDark 
              ? 'text-gray-400 hover:text-gray-200 hover:bg-gray-700' 
              : 'text-gray-600 hover:text-gray-800 hover:bg-gray-100'
          }`}
        >
          ✕
        </button>
      </div>

      {/* Content */}
      <div className="p-4">
        <div className="space-y-4">
          <div>
            <label className={`block text-sm font-medium mb-2 ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
              Custom Period
            </label>
            <input
              ref={customInputRef}
              type="text"
              placeholder="e.g. 40m, 4h, 1d, 1w, 1M, 1y"
              value={customTimeValue}
              onChange={(e) => onTimeValueChange(e.target.value)}
              onKeyPress={handleKeyPress}
              className={`w-full px-3 py-2 text-sm rounded-md border ${
                customTimeError
                  ? isDark 
                    ? 'bg-gray-900 border-red-500 text-gray-300 focus:ring-red-500' 
                    : 'bg-white border-red-500 text-gray-600 focus:ring-red-400'
                  : isDark 
                    ? 'bg-gray-900 border-gray-600 text-gray-300 focus:ring-cyan-500' 
                    : 'bg-white border-gray-300 text-gray-600 focus:ring-blue-400'
              } focus:outline-none focus:ring-2`}
            />
            {customTimeError && (
              <div className={`text-xs mt-1 ${isDark ? 'text-red-400' : 'text-red-600'}`}>
                {customTimeError}
              </div>
            )}
          </div>
          
          <div className={`text-xs ${isDark ? 'text-gray-400' : 'text-gray-500'}`}>
            <div className="mb-2">
              <div className="font-medium mb-1">Format:</div>
              <div className="leading-relaxed">
                <span className={isDark ? 'text-gray-300' : 'text-gray-600'}>40m • 2h • 1d • 1w • 1M • 1y</span>
              </div>
              <div className="mt-1 leading-relaxed">
                <span>Combined: </span>
                <span className={isDark ? 'text-gray-300' : 'text-gray-600'}>1h 30m, 1w 2d</span>
              </div>
              <div className="mt-1 text-[11px] italic">
                Note: Use uppercase M for months, lowercase m for minutes
              </div>
            </div>
            {maxTimeLimitInMinutes !== undefined && (
              <div className={`pt-2 border-t ${isDark ? 'border-gray-600' : 'border-gray-300'}`}>
                <div className={`font-medium ${isDark ? 'text-gray-300' : 'text-gray-600'}`}>
                  {maxTimeLimitInMinutes > 0 
                    ? `Max period: ${formatTimeWindow(maxTimeLimitInMinutes)}`
                    : 'Max period: Unlimited'}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className={`px-4 py-3 border-t flex justify-end gap-2 ${
        isDark ? 'border-gray-600' : 'border-gray-200'
      }`}>
        <button
          onClick={onCancel}
          className={`px-3 py-2 text-sm font-medium rounded-md transition-colors ${
            isDark 
              ? 'text-gray-300 hover:bg-gray-700' 
              : 'text-gray-700 hover:bg-gray-100'
          }`}
        >
          Cancel
        </button>
        <button
          onClick={onApply}
          disabled={!!customTimeError || !customTimeValue.trim()}
          className={`px-4 py-2 text-sm font-medium rounded-md transition-colors ${
            customTimeError || !customTimeValue.trim()
              ? isDark 
                ? 'bg-gray-700 text-gray-500 cursor-not-allowed' 
                : 'bg-gray-300 text-gray-500 cursor-not-allowed'
              : isDark 
                ? 'bg-cyan-600 text-white hover:bg-cyan-700' 
                : 'bg-blue-600 text-white hover:bg-blue-700'
          }`}
        >
          Apply
        </button>
      </div>
    </div>
  );
};
