// SPDX-License-Identifier: MIT
import React, { useRef, useEffect, useState } from 'react';
import { Button, TextInput } from '@nvidia/foundations-react-core';
import { IconInfoCircle } from '@tabler/icons-react';
import { TIME_WINDOW_OPTIONS, getCurrentTimeWindowLabel } from '../utils/timeUtils';
import { CustomTimeInput } from './CustomTimeInput';

const FETCH_SIZE_PRESETS = [50, 100, 200, 500, 1000, 2000, 5000];
const CUSTOM_SELECT_VALUE = '-1';

/* ------------------------------------------------------------------ */
/* Reusable inline custom-numeric-input for preset selects            */
/* ------------------------------------------------------------------ */

interface CustomNumericFieldProps {
  inputRef: React.RefObject<HTMLInputElement | null>;
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
          onValueChange={onValueChange}
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
  const customFetchRef = useRef<HTMLInputElement>(null);
  const [showCustomFetch, setShowCustomFetch] = useState(false);
  const [customFetchValue, setCustomFetchValue] = useState('');
  const [customFetchError, setCustomFetchError] = useState('');

  useEffect(() => {
    if (showCustomFetch && customFetchRef.current) customFetchRef.current.focus();
  }, [showCustomFetch]);

  const applyCustomFetch = () => {
    const num = Number(customFetchValue);
    if (!Number.isInteger(num) || num < 10 || num > 5000) {
      setCustomFetchError('Enter a number between 10 and 5000');
      return;
    }
    onFetchSizeChange(num);
    setShowCustomFetch(false);
    setCustomFetchError('');
  };

  if (!isOpen) return null;

  const label = `text-sm font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`;
  const selectCls = `rounded-lg pl-3 pr-8 py-1.5 text-sm focus:outline-none transition-all cursor-pointer ${
    isDark
      ? 'border border-gray-600 text-white hover:border-gray-500 focus:border-[#76b900] focus:ring-1 focus:ring-[#76b900]/40'
      : 'border border-gray-300 text-gray-600 focus:ring-green-400 hover:border-gray-400'
  }`;

  return (
    <>
      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <span className={label}>Query range</span>
          <span
            title="How far back to fetch alerts."
            aria-label="How far back to fetch alerts."
            className={isDark ? 'text-gray-400' : 'text-gray-500'}
          >
            <IconInfoCircle size={14} />
          </span>
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

      <div className="flex flex-col gap-2">
        <div className="flex items-center gap-1.5">
          <span className={label}>Fetch size</span>
          <span
            title="Max alerts per API call, higher values may be slower."
            aria-label="Max alerts per API call, higher values may be slower."
            className={isDark ? 'text-gray-400' : 'text-gray-500'}
          >
            <IconInfoCircle size={14} />
          </span>
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
    </>
  );
}
