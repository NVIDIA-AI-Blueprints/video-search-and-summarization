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
import { Button, TextInput } from '@nvidia/foundations-react-core';
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
          ? 'bg-black border-gray-600' 
          : 'bg-white border-gray-200'
      }`}
    >
      {/* Header */}
      <div className={`px-4 py-3 border-b flex items-center justify-between ${
        isDark ? 'border-gray-600' : 'border-gray-200'
      }`}>
        <div className="flex items-center gap-2">
          <IconRefresh className={`w-5 h-5 ${isDark ? 'text-green-400' : 'text-green-600'}`} />
          <span className={`text-sm font-medium ${isDark ? 'text-gray-200' : 'text-gray-800'}`}>
            Auto-Refresh Settings
          </span>
        </div>
        <button
          onClick={onClose}
          className="p-1.5 rounded transition-colors text-gray-400 hover:text-white hover:bg-neutral-700"
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
              className={`relative inline-flex h-8 w-16 items-center rounded-full transition-colors ${
                isEnabled
                  ? 'bg-[#76b900]'
                  : isDark ? 'bg-neutral-600' : 'bg-gray-300'
              }`}
              role="switch"
              aria-checked={isEnabled}
            >
              <span
                className={`inline-flex h-6 w-6 transform rounded-full bg-white transition items-center justify-center ${
                  isEnabled ? 'translate-x-9' : 'translate-x-1'
                }`}
              >
                {isEnabled ? (
                  <IconPlayerPlay className="w-3 h-3 text-green-600" />
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
              <TextInput
                ref={inputRef}
                type="number"
                min={1000}
                max={3600000}
                step={1000}
                placeholder="e.g. 1000, 5000, 10000"
                value={tempValue}
                onValueChange={(val: string) => handleInputChange(val)}
                disabled={!isEnabled}
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
                <Button
                  key={value}
                  kind="tertiary"
                  onClick={() => handleInputChange(value.toString())}
                  disabled={!isEnabled}
                >
                  {label}
                </Button>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

