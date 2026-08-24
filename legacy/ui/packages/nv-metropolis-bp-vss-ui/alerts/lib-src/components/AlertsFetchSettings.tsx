// SPDX-License-Identifier: MIT
import React, { useRef, useEffect, useState, useCallback } from 'react';
import { Button, TextInput } from '@nvidia/foundations-react-core';
import { IconSettings } from '@tabler/icons-react';
import { TIME_WINDOW_OPTIONS, getCurrentTimeWindowLabel } from '../utils/timeUtils';
import { CustomTimeInput } from './CustomTimeInput';

const FETCH_SIZE_PRESETS = [50, 100, 200, 500, 1000, 2000, 5000];
const CUSTOM_SELECT_VALUE = '-1';

/* ------------------------------------------------------------------ */
/* Reusable inline custom-numeric-input for preset selects            */
/* ------------------------------------------------------------------ */

interface CustomNumericFieldProps {
  inputRef: React.RefObject<HTMLInputElement>;
  min: number;
  max: number;
  value: string;
  error: string;
  isDark: boolean;
  onValueChange: (val: string) => void;
  onApply: () => void;
  onCancel: () => void;
}

function CustomNumericField({
  inputRef,
  min,
  max,
  value,
  error,
  isDark,
  onValueChange,
  onApply,
  onCancel,
}: CustomNumericFieldProps) {
  return (
    <>
      <div className="flex items-center gap-2">
        <TextInput
          ref={inputRef}
          type="number"
          min={min}
          max={max}
          placeholder={`${min} – ${max}`}
          value={value}
          onValueChange={(val: string) => {
            onValueChange(val);
          }}
          onKeyDown={(e: React.KeyboardEvent) => {
            if (e.key === 'Enter') onApply();
          }}
        />
        <Button kind="primary" onClick={onApply} disabled={!!error}>
          OK
        </Button>
        <Button kind="tertiary" onClick={onCancel}>
          ✕
        </Button>
      </div>
      {error && (
        <p className={`text-xs ${isDark ? 'text-red-400' : 'text-red-600'}`}>{error}</p>
      )}
    </>
  );
}

interface AlertsFetchSettingsProps {
  isOpen: boolean;
  isDark: boolean;
  onClose: () => void;
  timeWindow: number;
  onTimeWindowChange: (minutes: number) => void;
  showCustomTimeInput: boolean;
  customTimeValue: string;
  customTimeError: string;
  maxTimeLimitInMinutes?: number;
  onCustomTimeValueChange: (value: string) => void;
  onCustomTimeApply: () => void;
  onCustomTimeCancel: () => void;
  onOpenCustomTime: () => void;
  fetchSize: number;
  onFetchSizeChange: (size: number) => void;
}

export function AlertsFetchSettings({
  isOpen,
  isDark,
  onClose,
  timeWindow,
  onTimeWindowChange,
  showCustomTimeInput,
  customTimeValue,
  customTimeError,
  maxTimeLimitInMinutes,
  onCustomTimeValueChange,
  onCustomTimeApply,
  onCustomTimeCancel,
  onOpenCustomTime,
  fetchSize,
  onFetchSizeChange,
}: AlertsFetchSettingsProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const customFetchRef = useRef<HTMLInputElement>(null);
  const [showCustomFetch, setShowCustomFetch] = useState(false);
  const [customFetchValue, setCustomFetchValue] = useState('');
  const [customFetchError, setCustomFetchError] = useState('');

  useEffect(() => {
    if (showCustomFetch && customFetchRef.current) customFetchRef.current.focus();
  }, [showCustomFetch]);

  const applyCustomFetch = useCallback(() => {
    const num = Number(customFetchValue);
    if (!Number.isInteger(num) || num < 10 || num > 5000) {
      setCustomFetchError('Enter a number between 10 and 5000');
      return;
    }
    onFetchSizeChange(num);
    setShowCustomFetch(false);
    setCustomFetchError('');
  }, [customFetchValue, onFetchSizeChange]);

  useEffect(() => {
    if (!isOpen) return undefined;
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', handleClickOutside);
    document.addEventListener('keydown', handleEscape);
    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen, onClose]);

  if (!isOpen) return null;

  const border = isDark ? 'border-gray-600' : 'border-gray-200';
  const bg = isDark ? 'bg-black' : 'bg-white';
  const label = `text-sm font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`;
  const hint = `text-xs ${isDark ? 'text-gray-500' : 'text-gray-400'}`;
  const selectCls = `rounded-lg pl-3 pr-8 py-1.5 text-sm focus:outline-none transition-all cursor-pointer ${
    isDark
      ? 'bg-black border border-gray-600 text-white hover:border-gray-500 focus:border-[#76b900] focus:ring-1 focus:ring-[#76b900]/40'
      : 'bg-white border border-gray-300 text-gray-600 focus:ring-green-400 hover:border-gray-400'
  }`;

  return (
    <div
      ref={containerRef}
      className={`absolute top-full right-0 mt-2 w-80 rounded-lg shadow-lg border z-50 ${border} ${bg}`}
    >
      {/* Header */}
      <div className={`px-4 py-2.5 border-b flex items-center justify-between ${border}`}>
        <div className="flex items-center gap-2">
          <IconSettings className={`w-4 h-4 ${isDark ? 'text-green-400' : 'text-green-600'}`} />
          <span className={`text-sm font-medium ${isDark ? 'text-gray-200' : 'text-gray-800'}`}>
            Alerts Settings
          </span>
        </div>
        <button
          type="button"
          onClick={onClose}
          className={`p-1 rounded transition-colors text-gray-400 ${
            isDark ? 'hover:text-white hover:bg-neutral-700' : 'hover:text-gray-700 hover:bg-gray-200'
          }`}
        >
          ✕
        </button>
      </div>

      {/* Content */}
      <div className="px-4 py-3 space-y-3">
        {/* Period */}
        <div className="flex items-start justify-between gap-3">
          <div>
            <span className={label}>Query range</span>
            <p className={hint}>How far back to fetch alerts</p>
          </div>
          <select
            id="settings-period-select"
            data-testid="period-select"
            value={String(timeWindow)}
            className={selectCls}
            onChange={(e) => {
              const value = Number.parseInt(e.target.value, 10);
              if (value === -1) {
                onOpenCustomTime();
              } else {
                onTimeWindowChange(value);
              }
            }}
          >
            {TIME_WINDOW_OPTIONS.map((option) => (
              <option key={option.value} value={String(option.value)}>
                {option.label}
              </option>
            ))}
            {!TIME_WINDOW_OPTIONS.some((opt) => opt.value === timeWindow) && (
              <option value={String(timeWindow)}>
                {getCurrentTimeWindowLabel(timeWindow)}
              </option>
            )}
          </select>
        </div>
        <CustomTimeInput
          isOpen={showCustomTimeInput}
          timeWindow={timeWindow}
          customTimeValue={customTimeValue}
          customTimeError={customTimeError}
          isDark={isDark}
          maxTimeLimitInMinutes={maxTimeLimitInMinutes}
          onTimeValueChange={onCustomTimeValueChange}
          onApply={onCustomTimeApply}
          onCancel={onCustomTimeCancel}
        />

        {/* Fetch size */}
        <div className="flex items-start justify-between gap-3">
          <div>
            <span className={label}>Fetch size</span>
            <p className={hint}>Max alerts per API call</p>
            <p className={`text-xs italic ${isDark ? 'text-yellow-500/70' : 'text-yellow-600/70'}`}>Higher values may be slower</p>
          </div>
          <select
            value={FETCH_SIZE_PRESETS.includes(fetchSize) ? String(fetchSize) : CUSTOM_SELECT_VALUE}
            className={selectCls}
            onChange={(e) => {
              const v = e.target.value;
              if (v === CUSTOM_SELECT_VALUE) {
                setShowCustomFetch(true);
                setCustomFetchValue(String(fetchSize));
                setCustomFetchError('');
              } else {
                onFetchSizeChange(Number(v));
              }
            }}
          >
            {FETCH_SIZE_PRESETS.map((p) => (
              <option key={p} value={String(p)}>{p}</option>
            ))}
            {FETCH_SIZE_PRESETS.includes(fetchSize) ? (
              <option value={CUSTOM_SELECT_VALUE}>Custom</option>
            ) : (
              <option value={CUSTOM_SELECT_VALUE}>{fetchSize} (custom)</option>
            )}
          </select>
        </div>
        {showCustomFetch && (
          <CustomNumericField
            inputRef={customFetchRef}
            min={10}
            max={5000}
            value={customFetchValue}
            error={customFetchError}
            isDark={isDark}
            onValueChange={(val) => { setCustomFetchValue(val); setCustomFetchError(''); }}
            onApply={applyCustomFetch}
            onCancel={() => setShowCustomFetch(false)}
          />
        )}
      </div>
    </div>
  );
}
