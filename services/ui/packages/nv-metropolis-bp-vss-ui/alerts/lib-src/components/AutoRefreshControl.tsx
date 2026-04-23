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

import React, { useRef, useEffect, useState, useCallback } from 'react';
import { Button, TextInput } from '@nvidia/foundations-react-core';
import { IconRefresh, IconPlayerPlay, IconPlayerPause } from '@tabler/icons-react';

interface AutoRefreshControlProps {
  isOpen: boolean;
  isEnabled: boolean;
  interval: number; // in milliseconds
  isDark: boolean;
  /** When true, all settings are read-only (e.g. alerts table not on page 1). */
  controlsDisabled?: boolean;
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

function modalShellClassName(isDark: boolean): string {
  const theme = isDark ? 'bg-black border-gray-600' : 'bg-white border-gray-200';
  return `absolute top-full right-0 mt-2 w-96 rounded-lg shadow-lg border z-50 ${theme}`;
}

function toggleTrackBackgroundClass(isEnabled: boolean, isDark: boolean): string {
  if (isEnabled) {
    return 'bg-[#76b900]';
  }
  if (isDark) {
    return 'bg-neutral-600';
  }
  return 'bg-gray-300';
}

type ValidateIntervalResult =
  | { ok: true; value: number }
  | { ok: false; error: string };

function validateIntervalInput(value: string): ValidateIntervalResult {
  const numValue = Number.parseInt(value, 10);

  if (Number.isNaN(numValue)) {
    return { ok: false, error: 'Please enter a valid number' };
  }

  if (numValue < 1000) {
    return { ok: false, error: 'Minimum interval is 1000ms (1 second)' };
  }

  if (numValue > 3600000) {
    return { ok: false, error: 'Maximum interval is 3600000ms (1 hour)' };
  }

  return { ok: true, value: numValue };
}

function intervalErrorSurfaceClass(isDark: boolean): string {
  if (isDark) {
    return 'text-red-400 bg-red-500/10';
  }
  return 'text-red-600 bg-red-50 border border-red-200';
}

type LockedNoticeProps = Readonly<{ isDark: boolean }>;

function lockedNoticeSurfaceClass(isDark: boolean): string {
  if (isDark) {
    return 'bg-neutral-800 text-gray-300';
  }
  return 'bg-gray-100 text-gray-700';
}

function LockedSettingsNotice({ isDark }: LockedNoticeProps) {
  return (
    <output className={`text-xs block rounded-md px-3 py-2 ${lockedNoticeSurfaceClass(isDark)}`}>
      Go to page 1 of the alerts table to change auto-refresh or refresh manually.
    </output>
  );
}

type HeaderProps = Readonly<{ isDark: boolean; onClose: () => void }>;

function AutoRefreshModalHeader({ isDark, onClose }: HeaderProps) {
  const border = isDark ? 'border-gray-600' : 'border-gray-200';
  return (
    <div className={`px-4 py-3 border-b flex items-center justify-between ${border}`}>
      <div className="flex items-center gap-2">
        <IconRefresh className={`w-5 h-5 ${isDark ? 'text-green-400' : 'text-green-600'}`} />
        <span className={`text-sm font-medium ${isDark ? 'text-gray-200' : 'text-gray-800'}`}>
          Auto-Refresh Settings
        </span>
      </div>
      <button
        type="button"
        onClick={onClose}
        className="p-1.5 rounded transition-colors text-gray-400 hover:text-white hover:bg-neutral-700"
      >
        ✕
      </button>
    </div>
  );
}

type ToggleRowProps = Readonly<{
  isDark: boolean;
  isEnabled: boolean;
  settingsLocked: boolean;
  onToggle: () => void;
}>;

function toggleThumbTranslateClass(isEnabled: boolean): string {
  if (isEnabled) {
    return 'translate-x-9';
  }
  return 'translate-x-1';
}

function AutoRefreshToggleRow({ isDark, isEnabled, settingsLocked, onToggle }: ToggleRowProps) {
  const trackClass = toggleTrackBackgroundClass(isEnabled, isDark);
  const thumbClass = toggleThumbTranslateClass(isEnabled);
  return (
    <div className="flex items-center justify-between">
      <div>
        <span
          id="alerts-auto-refresh-heading"
          className={`block text-sm font-medium ${isDark ? 'text-gray-300' : 'text-gray-700'}`}
        >
          Auto-Refresh
        </span>
        <span className={`text-xs ${isDark ? 'text-gray-400' : 'text-gray-500'}`}>
          Automatically refresh data at intervals
        </span>
      </div>
      <button
        id="alerts-auto-refresh-switch"
        type="button"
        disabled={settingsLocked}
        onClick={() => {
          if (!settingsLocked) onToggle();
        }}
        className={`relative inline-flex h-8 w-16 items-center rounded-full transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${trackClass}`}
        role="switch"
        aria-checked={isEnabled}
        aria-labelledby="alerts-auto-refresh-heading"
      >
        <span
          className={`inline-flex h-6 w-6 transform rounded-full bg-white transition items-center justify-center ${thumbClass}`}
        >
          {isEnabled ? (
            <IconPlayerPlay className="w-3 h-3 text-green-600" />
          ) : (
            <IconPlayerPause className="w-3 h-3 text-gray-600" />
          )}
        </span>
      </button>
    </div>
  );
}

type IntervalBlockProps = Readonly<{
  isDark: boolean;
  tempValue: string;
  error: string;
  isEnabled: boolean;
  settingsLocked: boolean;
  interval: number;
  inputDisabled: boolean;
  inputRef: React.RefObject<HTMLInputElement>;
  onValueChange: (val: string) => void;
}>;

function AutoRefreshIntervalBlock({
  isDark,
  tempValue,
  error,
  isEnabled,
  settingsLocked,
  interval,
  inputDisabled,
  inputRef,
  onValueChange,
}: IntervalBlockProps) {
  return (
    <div>
      <label
        className={`block ${isDark ? 'text-gray-300' : 'text-gray-700'}`}
      >
        <span className="block text-sm font-medium mb-2">Refresh Interval</span>
        <div className="flex items-center gap-2">
          <TextInput
            ref={inputRef}
            id="alerts-auto-refresh-interval"
            type="number"
            min={1000}
            max={3600000}
            step={1000}
            placeholder="e.g. 1000, 5000, 10000"
            value={tempValue}
            onValueChange={(val: string) => onValueChange(val)}
            disabled={inputDisabled}
          />
          <span className={`text-sm font-medium ${isDark ? 'text-gray-400' : 'text-gray-600'}`}>ms</span>
        </div>
      </label>
      {error && (
        <div className={`text-xs mt-1 max-h-16 overflow-auto rounded p-2 break-words whitespace-pre-wrap ${intervalErrorSurfaceClass(isDark)}`}>
          {error}
        </div>
      )}
      {!error && isEnabled && !settingsLocked && (
        <div className={`text-xs mt-1 ${isDark ? 'text-gray-400' : 'text-gray-500'}`}>
          Refreshing every {formatInterval(interval)}
        </div>
      )}
    </div>
  );
}

type PresetsProps = Readonly<{
  isDark: boolean;
  inputDisabled: boolean;
  onPresetClick: (value: string) => void;
}>;

function AutoRefreshPresetsRow({ isDark, inputDisabled, onPresetClick }: PresetsProps) {
  const theme = isDark ? 'text-gray-400' : 'text-gray-500';
  return (
    <fieldset className={`text-xs text-left border-0 min-w-0 m-0 p-0 ${theme}`}>
      <legend className="mb-1 text-xs font-normal px-0">Quick presets:</legend>
      <div className="flex gap-2 flex-wrap">
        {PRESETS.map(([value, label]) => (
          <Button
            key={value}
            kind="tertiary"
            onClick={() => onPresetClick(value.toString())}
            disabled={inputDisabled}
          >
            {label}
          </Button>
        ))}
      </div>
    </fieldset>
  );
}

type UseAutoRefreshPanelArgs = Readonly<{
  isOpen: boolean;
  interval: number;
  controlsDisabled: boolean;
  onClose: () => void;
  onIntervalChange: (milliseconds: number) => void;
}>;

function useAutoRefreshPanelState({
  isOpen,
  interval,
  controlsDisabled,
  onClose,
  onIntervalChange,
}: UseAutoRefreshPanelArgs) {
  const containerRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [tempValue, setTempValue] = useState(() => interval.toString());
  const [error, setError] = useState('');

  useEffect(() => {
    if (!isOpen || !inputRef.current) {
      return;
    }
    inputRef.current.focus();
    setTempValue(interval.toString());
    setError('');
  }, [isOpen, interval]);

  useEffect(() => {
    if (!isOpen) {
      return undefined;
    }
    const handleClickOutside = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        onClose();
      }
    };
    const handleEscapeKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscapeKey);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscapeKey);
    };
  }, [isOpen, onClose]);

  const handleInputChange = useCallback(
    (value: string) => {
      if (controlsDisabled) {
        return;
      }
      setTempValue(value);
      const result = validateIntervalInput(value);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      setError('');
      onIntervalChange(result.value);
    },
    [controlsDisabled, onIntervalChange],
  );

  return { containerRef, inputRef, tempValue, error, handleInputChange };
}

export const AutoRefreshControl: React.FC<Readonly<AutoRefreshControlProps>> = ({
  isOpen,
  isEnabled,
  interval,
  isDark,
  controlsDisabled = false,
  onToggle,
  onIntervalChange,
  onClose,
}) => {
  const { containerRef, inputRef, tempValue, error, handleInputChange } = useAutoRefreshPanelState({
    isOpen,
    interval,
    controlsDisabled,
    onClose,
    onIntervalChange,
  });

  if (!isOpen) return null;

  const settingsLocked = controlsDisabled;
  const inputDisabled = settingsLocked || !isEnabled;

  return (
    <div ref={containerRef} className={modalShellClassName(isDark)}>
      <AutoRefreshModalHeader isDark={isDark} onClose={onClose} />
      <div className="p-4">
        <div className="space-y-4">
          {settingsLocked && <LockedSettingsNotice isDark={isDark} />}
          <AutoRefreshToggleRow
            isDark={isDark}
            isEnabled={isEnabled}
            settingsLocked={settingsLocked}
            onToggle={onToggle}
          />
          <AutoRefreshIntervalBlock
            isDark={isDark}
            tempValue={tempValue}
            error={error}
            isEnabled={isEnabled}
            settingsLocked={settingsLocked}
            interval={interval}
            inputDisabled={inputDisabled}
            inputRef={inputRef}
            onValueChange={handleInputChange}
          />
          <AutoRefreshPresetsRow
            isDark={isDark}
            inputDisabled={inputDisabled}
            onPresetClick={handleInputChange}
          />
        </div>
      </div>
    </div>
  );
};
