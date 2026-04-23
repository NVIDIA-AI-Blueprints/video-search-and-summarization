// SPDX-License-Identifier: MIT
/**
 * FilterControls component for the alerts system.
 * 
 * This component provides a comprehensive set of filtering controls for managing and viewing alerts.
 * It includes:
 * - VLM (Vision Language Model) verified toggle to filter verified/unverified alerts
 * - Time window selector with predefined options and custom time input capability
 * - Sensor filter dropdown to filter alerts by sensor
 * - Alert type filter dropdown to filter by alert classification
 * - Alert triggered filter dropdown to filter by trigger status
 * - Refresh button with loading state indicator
 * 
 * The component is fully theme-aware and supports both dark and light modes.
 */

import React, { useState } from 'react';
import { IconRefresh, IconRotateClockwise2 } from '@tabler/icons-react';
import { FilterType, VlmVerdict, VLM_VERDICT } from '../types';
import { TIME_WINDOW_OPTIONS, getCurrentTimeWindowLabel } from '../utils/timeUtils';
import { CustomTimeInput } from './CustomTimeInput';
import { AutoRefreshControl } from './AutoRefreshControl';
import { TimeFormatSwitch, type TimeFormat } from './TimeFormatSwitch';

export type { TimeFormat } from './TimeFormatSwitch';

interface FilterControlsProps {
  isDark: boolean;
  vlmVerified: boolean;
  vlmVerdict: VlmVerdict;
  timeWindow: number;
  timeFormat: TimeFormat;
  showCustomTimeInput: boolean;
  customTimeValue: string;
  customTimeError: string;
  maxTimeLimitInMinutes?: number;
  uniqueValues: {
    sensors: string[];
    alertTypes: string[];
    alertTriggered: string[];
  };
  loading: boolean;
  autoRefreshEnabled: boolean;
  autoRefreshInterval: number; // in milliseconds
  onVlmVerifiedChange: (verified: boolean) => void;
  onVlmVerdictChange: (verdict: VlmVerdict) => void;
  onTimeWindowChange: (minutes: number) => void;
  onTimeFormatChange: (format: TimeFormat) => void;
  onCustomTimeValueChange: (value: string) => void;
  onCustomTimeApply: () => void;
  onCustomTimeCancel: () => void;
  onOpenCustomTime: () => void;
  onAddFilter: (type: FilterType, value: string) => void;
  onRefresh: () => void;
  onAutoRefreshToggle: () => void;
  onAutoRefreshIntervalChange: (milliseconds: number) => void;
}

export const FilterControls: React.FC<FilterControlsProps> = ({
  isDark,
  vlmVerified,
  vlmVerdict,
  timeWindow,
  timeFormat,
  showCustomTimeInput,
  customTimeValue,
  customTimeError,
  maxTimeLimitInMinutes,
  uniqueValues,
  loading,
  autoRefreshEnabled,
  autoRefreshInterval,
  onVlmVerifiedChange,
  onVlmVerdictChange,
  onTimeWindowChange,
  onTimeFormatChange,
  onCustomTimeValueChange,
  onCustomTimeApply,
  onCustomTimeCancel,
  onOpenCustomTime,
  onAddFilter,
  onRefresh,
  onAutoRefreshToggle,
  onAutoRefreshIntervalChange
}) => {
  const [showAutoRefreshControl, setShowAutoRefreshControl] = useState(false);
  const selectClass = `rounded-lg pl-3 pr-8 py-2 text-sm focus:outline-none transition-all cursor-pointer ${
    isDark 
      ? 'bg-black border border-gray-600 text-white hover:border-gray-500 focus:border-[#76b900] focus:ring-1 focus:ring-[#76b900]/40' 
      : 'bg-white border border-gray-300 text-gray-600 focus:ring-green-400 hover:border-gray-400'
  }`;

  return (
    <div className="flex items-center gap-2 my-1">
      <div className="flex items-center gap-2 flex-wrap flex-1 min-w-0">
      <div className={`flex items-center gap-3 px-3.5 py-1.5 rounded-lg transition-all ${
        isDark 
          ? 'bg-black/30 hover:bg-black/40' 
          : 'bg-gray-100/60 hover:bg-gray-100'
      }`}>
        <div className="flex items-center gap-2">
          <label htmlFor="vlm-verified-toggle" className={`text-sm font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
            VLM Verified
          </label>
          <button
            id="vlm-verified-toggle"
            type="button"
            role="switch"
            aria-checked={vlmVerified}
            onClick={() => onVlmVerifiedChange(!vlmVerified)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
              (() => {
                if (vlmVerified) return 'bg-[#76b900]';
                if (isDark) return 'bg-slate-600';
                return 'bg-gray-300';
              })()
            }`}
            data-testid="vlm-verified-toggle"
          >
            <span
              className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow transition duration-200 ease-in-out ${
                vlmVerified ? 'translate-x-5' : 'translate-x-0.5'
              }`}
            />
          </button>
        </div>

        {/* VLM Verdict Filter - Only show when vlmVerified is true */}
        {vlmVerified && (
          <>
            <div className={`h-5 w-px ${isDark ? 'bg-gray-600/50' : 'bg-gray-300/70'}`} />
            <div className="flex items-center gap-2">
              <label htmlFor="vlm-verdict-select" className={`text-sm font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
                Verdict:
              </label>
              <select
                id="vlm-verdict-select"
                className={`${selectClass} min-w-[180px]`}
                value={vlmVerdict}
                onChange={(e) => onVlmVerdictChange(e.target.value as VlmVerdict)}
              >
                <option value={VLM_VERDICT.ALL}>All</option>
                <option value={VLM_VERDICT.CONFIRMED}>Confirmed</option>
                <option value={VLM_VERDICT.NOT_CONFIRMED}>Not Confirmed</option>
                <option value={VLM_VERDICT.REJECTED}>Rejected</option>
                <option value={VLM_VERDICT.VERIFICATION_FAILED}>Verification Failed</option>
              </select>
            </div>
          </>
        )}
      </div>

      {/* Time Window Filter */}
      <div className="relative flex items-center gap-2">
        <label htmlFor="period-select" className={`text-sm font-medium ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
          Period:
        </label>
        <select
          id="period-select"
          data-testid="period-select"
          value={String(timeWindow)}
          className={`${selectClass} min-w-[180px]`}
          onChange={(e) => {
            const value = Number.parseInt(e.target.value, 10);
            if (value === -1) {
              onOpenCustomTime();
            } else {
              onTimeWindowChange(value);
            }
          }}
        >
          {TIME_WINDOW_OPTIONS.map(option => (
            <option key={option.value} value={String(option.value)}>
              {option.label}
            </option>
          ))}
          {!TIME_WINDOW_OPTIONS.some(opt => opt.value === timeWindow) && (
            <option value={String(timeWindow)}>
              {getCurrentTimeWindowLabel(timeWindow)}
            </option>
          )}
        </select>
        
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
      </div>

      <TimeFormatSwitch
        value={timeFormat}
        onChange={onTimeFormatChange}
        isDark={isDark}
      />

      {/* Sensor Filter */}
      <select 
        data-testid="sensor-select"
        className={`${selectClass} min-w-[180px]`}
        onChange={(e) => {
          const value = e.target.value;
          if (value) {
            onAddFilter('sensors', value);
          }
          e.target.value = '';
        }}
      >
        <option value="">Sensor...</option>
        {uniqueValues.sensors
          .filter(sensor => sensor && sensor.trim() !== '')
          .map(sensor => (
            <option key={sensor} value={sensor}>{sensor}</option>
          ))}
      </select>

      {/* Alert Type Filter */}
      <select 
        data-testid="alert-type-select"
        className={`${selectClass} min-w-[180px]`}
        onChange={(e) => {
          const value = e.target.value;
          if (value) {
            onAddFilter('alertTypes', value);
          }
          e.target.value = '';
        }}
      >
        <option value="">Alert Type...</option>
        {uniqueValues.alertTypes
          .filter(type => type && type.trim() !== '')
          .map(type => (
            <option key={type} value={type}>{type}</option>
          ))}
      </select>

      {/* Alert Triggered Filter */}
      <select 
        data-testid="alert-triggered-select"
        className={`${selectClass} min-w-[180px]`}
        onChange={(e) => {
          const value = e.target.value;
          if (value) {
            onAddFilter('alertTriggered', value);
          }
          e.target.value = '';
        }}
      >
        <option value="">Alert Triggered...</option>
        {uniqueValues.alertTriggered
          .filter(triggered => triggered && triggered.trim() !== '')
          .map(triggered => (
            <option key={triggered} value={triggered}>{triggered}</option>
          ))}
      </select>

      </div>
      {/* Auto-Refresh and Refresh Controls */}
      <div className="relative flex items-center gap-2 flex-shrink-0">
        {/* Auto-Refresh Settings Button */}
        <button
          onClick={() => setShowAutoRefreshControl(!showAutoRefreshControl)}
          className={`p-2 rounded transition-colors relative ${
            isDark
              ? 'text-gray-300 hover:bg-neutral-700 hover:text-white'
              : 'text-gray-600 hover:bg-gray-200 hover:text-gray-900'
          }`}
          title={autoRefreshEnabled ? `Auto-refresh: ${autoRefreshInterval}ms` : 'Auto-refresh disabled'}
        >
          <IconRotateClockwise2 className="w-4 h-4" />
          {autoRefreshEnabled && (
            <span data-testid="auto-refresh-indicator" className="absolute -top-1 -right-1 w-2 h-2 rounded-full bg-green-400 animate-pulse" />
          )}
        </button>

        {/* Manual Refresh Button */}
        <button
          onClick={onRefresh}
          className={`p-2 rounded transition-colors ${
            isDark
              ? 'text-gray-300 hover:bg-neutral-700 hover:text-white'
              : 'text-gray-600 hover:bg-gray-200 hover:text-gray-900'
          }`}
          title="Refresh now"
        >
          <IconRefresh className={`w-4 h-4 ${loading ? 'animate-spin [animation-direction:reverse]' : ''}`} />
        </button>

        {/* Auto-Refresh Control Modal */}
        <AutoRefreshControl
          isOpen={showAutoRefreshControl}
          isEnabled={autoRefreshEnabled}
          interval={autoRefreshInterval}
          isDark={isDark}
          onToggle={onAutoRefreshToggle}
          onIntervalChange={onAutoRefreshIntervalChange}
          onClose={() => setShowAutoRefreshControl(false)}
        />
      </div>
    </div>
  );
};
