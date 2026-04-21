// SPDX-License-Identifier: MIT
/**
 * AutoRefreshControl Component - Advanced Auto-Refresh Configuration Interface
 * 
 * This component provides a modal interface for configuring auto-refresh settings
 * in the alerts management system. It offers a professional, user-friendly interface
 * for managing auto-refresh intervals with real-time updates.
 * 
 * **Key Features:**
 * - Modal-based interface with professional styling and animations
 * - Enable/disable toggle for auto-refresh functionality
 * - Configurable refresh interval in milliseconds with instant apply
 * - Real-time validation with immediate user feedback
 * - Quick preset buttons (1s, 5s, 10s, 30s, 1m)
 * - Auto-focus functionality for enhanced user experience
 * - Smart click-outside and keyboard interaction handling (Escape key support)
 * - Theme support for both light and dark modes
 * - Resets to default value on page refresh
 * 
 * **Input Format:**
 * - Accepts milliseconds (e.g., 1000 for 1 second, 5000 for 5 seconds)
 * - Minimum value: 1000ms (1 second)
 * - Maximum value: 3600000ms (1 hour)
 * - Changes are applied immediately (no need for confirmation)
 */

import React, { useRef, useEffect, useState } from 'react';
import { IconRefresh, IconPlayerPlay, IconPlayerPause } from '@tabler/icons-react';

interface AutoRefreshControlProps {
  isOpen: boolean;
  isEnabled: boolean;
  interval: number; // in milliseconds
  isDark: boolean;
  onToggle: () => void;
  onIntervalChange: (milliseconds: number) => void;
  onClose: () => void;
}

// Quick preset values: [milliseconds, label]
const PRESETS = [
  [1000, '1s'],
  [5000, '5s'],
  [10000, '10s'],
  [30000, '30s'],
  [60000, '1m'],
] as const;

// Helper function to format interval
const formatInterval = (ms: number): string => {
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  return `${(ms / 60000).toFixed(1)}m`;
};

export const AutoRefreshControl: React.FC<AutoRefreshControlProps> = ({
  isOpen,
  isEnabled,
  interval,
  isDark,
  onToggle,
  onIntervalChange,
  onClose
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [tempValue, setTempValue] = useState<string>(interval.toString());
  const [error, setError] = useState<string>('');

  // Auto-focus when opened
  useEffect(() => {
    if (isOpen && inputRef.current) {
      inputRef.current.focus();
      setTempValue(interval.toString());
      setError('');
    }
  }, [isOpen, interval]);

  // Handle click outside and escape key
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        if (isOpen) {
          onClose();
        }
      }
    };

    const handleEscapeKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && isOpen) {
        onClose();
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
  }, [isOpen, onClose]);

  const validateAndApply = (value: string) => {
    const numValue = parseInt(value);
    
    if (isNaN(numValue)) {
      setError('Please enter a valid number');
      return false;
    }
    
    if (numValue < 1000) {
      setError('Minimum interval is 1000ms (1 second)');
      return false;
    }
    
    if (numValue > 3600000) {
      setError('Maximum interval is 3600000ms (1 hour)');
      return false;
    }
    
    setError('');
    onIntervalChange(numValue);
    return true;
  };

  const handleInputChange = (value: string) => {
    setTempValue(value);
    validateAndApply(value);
  };

  if (!isOpen) return null;

  return (
    <div 
      ref={containerRef} 
      className={`absolute top-full right-0 mt-2 w-96 rounded-lg shadow-lg border z-50 ${
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
          <IconRefresh className={`w-5 h-5 ${isDark ? 'text-cyan-400' : 'text-blue-600'}`} />
          <span className={`text-sm font-medium ${isDark ? 'text-gray-200' : 'text-gray-800'}`}>
            Auto-Refresh Settings
          </span>
        </div>
        <button
          onClick={onClose}
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
          {/* Enable/Disable Toggle */}
          <div className="flex items-center justify-between">
            <div>
              <label className={`block text-sm font-medium ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
                Auto-Refresh
              </label>
              <span className={`text-xs ${isDark ? 'text-gray-400' : 'text-gray-500'}`}>
                Automatically refresh data at intervals
              </span>
            </div>
            <button
              onClick={onToggle}
              className={`relative inline-flex h-8 w-14 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 ${
                isEnabled
                  ? isDark ? 'bg-cyan-600 focus:ring-cyan-500' : 'bg-blue-600 focus:ring-blue-500'
                  : isDark ? 'bg-gray-600 focus:ring-gray-500' : 'bg-gray-300 focus:ring-gray-500'
              } ${isDark ? 'focus:ring-offset-gray-800' : 'focus:ring-offset-white'}`}
            >
              <span
                className={`inline-block h-6 w-6 transform rounded-full bg-white transition flex items-center justify-center ${
                  isEnabled ? 'translate-x-7' : 'translate-x-1'
                }`}
              >
                {isEnabled ? (
                  <IconPlayerPlay className="w-3 h-3 text-blue-600" />
                ) : (
                  <IconPlayerPause className="w-3 h-3 text-gray-600" />
                )}
              </span>
            </button>
          </div>

          {/* Interval Input */}
          <div>
            <label className={`block text-sm font-medium mb-2 ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
              Refresh Interval
            </label>
            <div className="flex items-center gap-2">
              <input
                ref={inputRef}
                type="number"
                min="1000"
                max="3600000"
                step="1000"
                placeholder="e.g. 1000, 5000, 10000"
                value={tempValue}
                onChange={(e) => handleInputChange(e.target.value)}
                disabled={!isEnabled}
                className={`flex-1 px-3 py-2 text-sm rounded-md border ${
                  error
                    ? isDark 
                      ? 'bg-gray-900 border-red-500 text-gray-300 focus:ring-red-500' 
                      : 'bg-white border-red-500 text-gray-600 focus:ring-red-400'
                    : isDark 
                      ? 'bg-gray-900 border-gray-600 text-gray-300 focus:ring-cyan-500' 
                      : 'bg-white border-gray-300 text-gray-600 focus:ring-blue-400'
                } focus:outline-none focus:ring-2 disabled:opacity-50 disabled:cursor-not-allowed`}
              />
              <span className={`text-sm font-medium ${isDark ? 'text-gray-400' : 'text-gray-600'}`}>
                ms
              </span>
            </div>
            {error && (
              <div className={`text-xs mt-1 max-h-16 overflow-auto rounded p-2 break-words whitespace-pre-wrap ${isDark ? 'text-red-400 bg-red-500/10' : 'text-red-600 bg-red-50 border border-red-200'}`}>
                {error}
              </div>
            )}
            {!error && isEnabled && (
              <div className={`text-xs mt-1 ${isDark ? 'text-gray-400' : 'text-gray-500'}`}>
                Refreshing every {formatInterval(interval)}
              </div>
            )}
          </div>
          
          <div className={`text-xs ${isDark ? 'text-gray-400' : 'text-gray-500'}`}>
            <div className="mb-1">Quick presets:</div>
            <div className="flex gap-2 flex-wrap">
              {PRESETS.map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => handleInputChange(value.toString())}
                  disabled={!isEnabled}
                  className={`px-2 py-1 rounded ${
                    isDark 
                      ? 'bg-gray-700 hover:bg-gray-600 text-gray-300' 
                      : 'bg-gray-100 hover:bg-gray-200 text-gray-700'
                  } disabled:opacity-50 disabled:cursor-not-allowed`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

