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

export type { TimeFormat };

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
  const selectClass = `rounded-md pl-3 pr-8 py-2 text-sm focus:outline-none focus:ring-2 transition-all cursor-pointer ${
    isDark 
      ? 'bg-gray-900 border border-gray-600 text-gray-300 focus:ring-cyan-500 hover:border-gray-500' 
      : 'bg-white border border-gray-300 text-gray-600 focus:ring-blue-400 hover:border-gray-400'
  }`;

  return (
    <div className="flex items-center gap-2 my-1 flex-wrap">
      {/* VLM Verified Toggle with Verdict Filter */}
      <div className={`flex items-center gap-3 px-3.5 py-1.5 rounded-lg transition-all ${
        isDark 
          ? 'bg-gray-700/30 hover:bg-gray-700/40' 
          : 'bg-gray-100/60 hover:bg-gray-100'
      }`}>
        <div className="flex items-center gap-2">
          <label className={`text-sm font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
            VLM Verified
          </label>
          <button
            onClick={() => onVlmVerifiedChange(!vlmVerified)}
            className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-offset-2 ${
              vlmVerified
                ? isDark ? 'bg-cyan-600 focus:ring-cyan-500' : 'bg-blue-600 focus:ring-blue-500'
                : isDark ? 'bg-gray-600 focus:ring-gray-500' : 'bg-gray-200 focus:ring-gray-500'
            } ${isDark ? 'focus:ring-offset-gray-800' : 'focus:ring-offset-white'}`}
          >
            <span
              className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${
                vlmVerified ? 'translate-x-6' : 'translate-x-1'
              }`}
            />
          </button>
        </div>

        {/* VLM Verdict Filter - Only show when vlmVerified is true */}
        {vlmVerified && (
          <>
            <div className={`h-5 w-px ${isDark ? 'bg-gray-600/50' : 'bg-gray-300/70'}`} />
            <div className="flex items-center gap-2">
              <label className={`text-sm font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
                Verdict:
              </label>
              <select 
                className={selectClass}
                value={vlmVerdict}
                onChange={(e) => onVlmVerdictChange(e.target.value as VlmVerdict)}
              >
                <option value={VLM_VERDICT.ALL}>All</option>
                <option value={VLM_VERDICT.CONFIRMED}>Confirmed</option>
                <option value={VLM_VERDICT.REJECTED}>Rejected</option>
                <option value={VLM_VERDICT.VERIFICATION_FAILED}>Verification Failed</option>
              </select>
            </div>
          </>
        )}
      </div>

      {/* Time Window Filter */}
      <div className="relative flex items-center gap-2">
        <label className={`text-sm font-medium ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
          Period:
        </label>
        <select 
          className={selectClass}
          value={timeWindow}
          onChange={(e) => {
            const value = parseInt(e.target.value);
            if (value === -1) {
              onOpenCustomTime();
            } else {
              onTimeWindowChange(value);
            }
          }}
        >
          {TIME_WINDOW_OPTIONS.map(option => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
          {!TIME_WINDOW_OPTIONS.find(opt => opt.value === timeWindow) && (
            <option key={`custom-${timeWindow}`} value={timeWindow}>
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
        className={selectClass}
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
        className={selectClass}
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
        className={selectClass}
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

      {/* Auto-Refresh and Refresh Controls */}
      <div className="relative flex items-center gap-2 ml-auto">
        {/* Auto-Refresh Settings Button */}
        <button
          onClick={() => setShowAutoRefreshControl(!showAutoRefreshControl)}
          className={`p-2 rounded transition-colors relative ${
            autoRefreshEnabled
              ? isDark 
                ? 'bg-cyan-600 text-white hover:bg-cyan-700' 
                : 'bg-blue-600 text-white hover:bg-blue-700'
              : isDark 
                ? 'text-gray-400 hover:bg-gray-700 hover:text-gray-200' 
                : 'text-gray-600 hover:bg-gray-200 hover:text-gray-900'
          }`}
          title={autoRefreshEnabled ? `Auto-refresh: ${autoRefreshInterval}ms` : 'Auto-refresh disabled'}
        >
          <IconRotateClockwise2 className="w-4 h-4" />
          {autoRefreshEnabled && (
            <span className={`absolute -top-1 -right-1 w-2 h-2 rounded-full ${
              isDark ? 'bg-cyan-400' : 'bg-blue-400'
            } animate-pulse`} />
          )}
        </button>

        {/* Manual Refresh Button */}
        <button
          onClick={onRefresh}
          className={`p-2 rounded transition-colors ${
            isDark 
              ? 'text-gray-400 hover:bg-gray-700 hover:text-gray-200' 
              : 'text-gray-600 hover:bg-gray-200 hover:text-gray-900'
          }`}
          title="Refresh now"
        >
          <IconRefresh className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
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
