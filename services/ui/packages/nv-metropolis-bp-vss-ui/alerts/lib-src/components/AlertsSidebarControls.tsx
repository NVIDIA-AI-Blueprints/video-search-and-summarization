// SPDX-License-Identifier: MIT
/**
 * Alerts sidebar controls — sub-view tablist plus view/create controls.
 */

import React from 'react';
import { Button } from 'rsuite';
import { Tag as KaizenTag, Select } from '@nvidia/foundations-react-core';
import { IconChevronDown, IconChevronUp, IconEye, IconFilter, IconPencilPlus, IconPlus, IconSettings } from '@tabler/icons-react';
import { IconX } from '@tabler/icons-react';
import { AlertRulesType, AlertsView, FilterState, FilterType, VlmVerdict, VLM_VERDICT } from '../types';
import { AlertsFetchSettings } from './AlertsFetchSettings';

interface AlertsSidebarControlsProps {
  isDark: boolean;
  alertsView: AlertsView;
  onAlertsViewChange: (view: AlertsView) => void;
  onAddNewAlertRule: () => void;
  manageAlertsEnabled?: boolean;
  vlmVerified: boolean;
  vlmVerdict: VlmVerdict;
  uniqueValues: {
    sensors: string[];
    alertTypes: string[];
    alertTriggered: string[];
    byVlmVerified?: {
      enabled: { alertTypes: string[]; alertTriggered: string[] };
      disabled: { alertTypes: string[]; alertTriggered: string[] };
    };
  };
  onVlmVerifiedChange: (verified: boolean) => void;
  onVlmVerdictChange: (verdict: VlmVerdict) => void;
  onAddFilter: (type: FilterType, value: string) => void;
  activeFilters: FilterState;
  onRemoveFilter: (type: FilterType, filter: string) => void;
  onClearAllFilters: () => void;
  timeWindow: number;
  showCustomTimeInput: boolean;
  customTimeValue: string;
  customTimeError: string;
  maxTimeLimitInMinutes?: number;
  onTimeWindowChange: (minutes: number) => void;
  onCustomTimeValueChange: (value: string) => void;
  onCustomTimeApply: () => void;
  onCustomTimeCancel: () => void;
  onOpenCustomTime: () => void;
  fetchSize: number;
  onFetchSizeChange: (size: number) => void;
  createActiveKind: AlertRulesType;
  streamFilter: string;
  typeFilter: string;
  onStreamFilterChange: (value: string) => void;
  onTypeFilterChange: (value: string) => void;
}

const ALERTS_VIEW_OPTIONS: Array<{
  id: AlertsView;
  label: string;
  icon: React.ReactNode;
}> = [
  { id: 'view', label: 'View Alerts', icon: <IconEye size={16} /> },
  { id: 'create', label: 'Manage Alerts', icon: <IconPencilPlus size={16} /> },
];

/** Stable id of the panel each tab controls. Matched in AlertsComponent. */
export const ALERTS_VIEW_PANEL_ID: Record<AlertsView, string> = {
  view: 'alerts-panel-view',
  create: 'alerts-panel-create',
};

const tabId = (view: AlertsView) => `alerts-tab-${view}`;

interface AlertsViewFilterControlsProps {
  isDark: boolean;
  vlmVerified: boolean;
  vlmVerdict: VlmVerdict;
  uniqueValues: AlertsSidebarControlsProps['uniqueValues'];
  onVlmVerifiedChange: (verified: boolean) => void;
  onVlmVerdictChange: (verdict: VlmVerdict) => void;
  onAddFilter: (type: FilterType, value: string) => void;
  activeFilters: FilterState;
  onRemoveFilter: (type: FilterType, filter: string) => void;
  onClearAllFilters: () => void;
  timeWindow: number;
  showCustomTimeInput: boolean;
  customTimeValue: string;
  customTimeError: string;
  maxTimeLimitInMinutes?: number;
  onTimeWindowChange: (minutes: number) => void;
  onCustomTimeValueChange: (value: string) => void;
  onCustomTimeApply: () => void;
  onCustomTimeCancel: () => void;
  onOpenCustomTime: () => void;
  fetchSize: number;
  onFetchSizeChange: (size: number) => void;
}

const getKaizenTagStyle = (type: FilterType, isDark: boolean): React.CSSProperties => {
  if (isDark) {
    if (type === 'sensors') return { color: '#4ade80', borderColor: '#22c55e', backgroundColor: 'transparent' };
    if (type === 'alertTypes') return { color: '#fb923c', borderColor: '#f97316', backgroundColor: 'transparent' };
    return { color: '#34d399', borderColor: '#10b981', backgroundColor: 'transparent' };
  }

  if (type === 'sensors') return { color: '#15803d', borderColor: '#86efac', backgroundColor: '#dcfce7' };
  if (type === 'alertTypes') return { color: '#7e22ce', borderColor: '#d8b4fe', backgroundColor: '#f3e8ff' };
  return { color: '#047857', borderColor: '#6ee7b7', backgroundColor: '#d1fae5' };
};

const FILTER_TAG_CONFIGS: Array<{ type: FilterType; keyPrefix: string }> = [
  { type: 'sensors', keyPrefix: 'sensor' },
  { type: 'alertTypes', keyPrefix: 'alertType' },
];

export const AlertsViewFilterControls: React.FC<AlertsViewFilterControlsProps> = ({
  isDark,
  vlmVerified,
  vlmVerdict,
  uniqueValues,
  onVlmVerifiedChange,
  onVlmVerdictChange,
  onAddFilter,
  activeFilters,
  onRemoveFilter,
  onClearAllFilters,
  timeWindow,
  showCustomTimeInput,
  customTimeValue,
  customTimeError,
  maxTimeLimitInMinutes,
  onTimeWindowChange,
  onCustomTimeValueChange,
  onCustomTimeApply,
  onCustomTimeCancel,
  onOpenCustomTime,
  fetchSize,
  onFetchSizeChange,
}) => {
  const [isFilterOpen, setIsFilterOpen] = React.useState(true);
  const [isSettingsOpen, setIsSettingsOpen] = React.useState(false);
  const hasActiveFilters = FILTER_TAG_CONFIGS.some(({ type }) => activeFilters[type].size > 0);
  const alertTypeOptions = uniqueValues.byVlmVerified
    ? uniqueValues.byVlmVerified[vlmVerified ? 'enabled' : 'disabled'].alertTypes
    : uniqueValues.alertTypes;
  const selectClass = `rounded-lg pl-3 pr-8 py-2 text-sm focus:outline-none transition-all cursor-pointer ${
    isDark
      ? 'border border-gray-600 text-white hover:border-gray-500 focus:border-[#76b900] focus:ring-1 focus:ring-[#76b900]/40'
      : 'border border-gray-300 text-gray-600 focus:ring-green-400 hover:border-gray-400'
  }`;

  return (
    <div className="flex flex-col gap-3">
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

      {vlmVerified && (
        <div className="relative flex flex-col gap-1.5 w-full">
          <Select
            id="vlm-verdict-select"
            data-testid="vlm-verdict-select"
            value={vlmVerdict}
            onValueChange={(value) => {
              if (value) onVlmVerdictChange(value as VlmVerdict);
            }}
            items={[
              { value: VLM_VERDICT.ALL, children: 'All' },
              { value: VLM_VERDICT.CONFIRMED, children: 'Confirmed' },
              { value: VLM_VERDICT.REJECTED, children: 'Rejected' },
              { value: VLM_VERDICT.VERIFICATION_FAILED, children: 'Verification Failed' },
            ]}
          />
        </div>
      )}
      <div
        className={`flex flex-col rounded-lg border ${
          isDark ? 'border-gray-700 bg-neutral-900' : 'border-gray-300 bg-white'
        }`}
      >
        <button
          type="button"
          onClick={() => setIsFilterOpen((prev) => !prev)}
          aria-expanded={isFilterOpen}
          data-testid="alerts-filters-toggle"
          className={`w-full px-3.5 py-2.5 min-h-[44px] flex items-center gap-2 text-left transition-colors ${
            isDark ? 'text-gray-100 hover:bg-neutral-800' : 'text-gray-800 hover:bg-gray-50'
          }`}
        >
          {isFilterOpen ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
          <IconFilter size={16} />
          <span className="text-sm font-semibold">Filters</span>
        </button>

        {isFilterOpen && (
          <div className={`flex flex-col gap-2 px-3.5 pb-3 pt-2 ${isDark ? 'border-t border-gray-700' : 'border-t border-gray-200'}`}>
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
              {alertTypeOptions
                .filter(type => type && type.trim() !== '')
                .map(type => (
                  <option key={type} value={type}>{type}</option>
                ))}
            </select>
          </div>
        )}
      </div>

      {hasActiveFilters && (
        <div
          data-testid="alerts-filter-tags"
          style={{
            display: 'flex',
            flexWrap: 'wrap',
            gap: 6,
            alignItems: 'center',
          }}
        >
          {FILTER_TAG_CONFIGS.flatMap(({ type, keyPrefix }) =>
            Array.from(activeFilters[type]).map((filter) => (
              <KaizenTag
                key={`${keyPrefix}-${filter}`}
                kind="outline"
                color="gray"
                onClick={() => onRemoveFilter(type, filter)}
                aria-label={`Remove filter ${filter}`}
                data-testid={`alerts-filter-tag-${keyPrefix}-${filter.replace(/\s+/g, '-').toLowerCase()}`}
                style={{
                  ...getKaizenTagStyle(type, isDark),
                  borderWidth: 1,
                  borderStyle: 'solid',
                  padding: '6px 10px',
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: 6,
                  cursor: 'pointer',
                }}
              >
                {filter}
                <IconX size={14} />
              </KaizenTag>
            ))
          )}

          <Button
            size="sm"
            appearance="primary"
            color="red"
            onClick={onClearAllFilters}
          >
            Clear All
          </Button>
        </div>
      )}

      <div
        className={`flex flex-col rounded-lg border ${
          isDark ? 'border-gray-700 bg-neutral-900' : 'border-gray-300 bg-white'
        }`}
      >
        <button
          type="button"
          onClick={() => {
            setIsSettingsOpen((prev) => !prev);
          }}
          aria-expanded={isSettingsOpen}
          data-testid="alerts-settings-toggle"
          className={`w-full px-3.5 py-2.5 min-h-[44px] flex items-center gap-2 text-left transition-colors ${
            isDark ? 'text-gray-100 hover:bg-neutral-800' : 'text-gray-800 hover:bg-gray-50'
          }`}
        >
          {isSettingsOpen ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
          <IconSettings size={16} />
          <span className="text-sm font-semibold">Settings</span>
        </button>

        {isSettingsOpen && (
          <div className={`relative px-3.5 pb-3 pt-2 ${isDark ? 'border-t border-gray-700' : 'border-t border-gray-200'}`}>
            <AlertsFetchSettings
              isOpen={true}
              isDark={isDark}
              timeWindow={timeWindow}
              onTimeWindowChange={onTimeWindowChange}
              showCustomTimeInput={showCustomTimeInput}
              customTimeValue={customTimeValue}
              customTimeError={customTimeError}
              maxTimeLimitInMinutes={maxTimeLimitInMinutes}
              onCustomTimeValueChange={onCustomTimeValueChange}
              onCustomTimeApply={onCustomTimeApply}
              onCustomTimeCancel={onCustomTimeCancel}
              onOpenCustomTime={onOpenCustomTime}
              fetchSize={fetchSize}
              onFetchSizeChange={onFetchSizeChange}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export const AlertsSidebarControls: React.FC<AlertsSidebarControlsProps> = ({
  isDark,
  alertsView,
  onAlertsViewChange,
  onAddNewAlertRule,
  manageAlertsEnabled = true,
  vlmVerified,
  vlmVerdict,
  uniqueValues,
  onVlmVerifiedChange,
  onVlmVerdictChange,
  onAddFilter,
  activeFilters,
  onRemoveFilter,
  onClearAllFilters,
  timeWindow,
  showCustomTimeInput,
  customTimeValue,
  customTimeError,
  maxTimeLimitInMinutes,
  onTimeWindowChange,
  onCustomTimeValueChange,
  onCustomTimeApply,
  onCustomTimeCancel,
  onOpenCustomTime,
  fetchSize,
  onFetchSizeChange,
  createActiveKind,
  streamFilter,
  typeFilter,
  onStreamFilterChange,
  onTypeFilterChange,
}) => {
  const tabRefs = React.useRef<Array<HTMLButtonElement | null>>([]);
  const viewOptions = manageAlertsEnabled
    ? ALERTS_VIEW_OPTIONS
    : ALERTS_VIEW_OPTIONS.filter((option) => option.id !== 'create');

  const handleTabKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, currentIndex: number) => {
    let nextIndex: number | null = null;
    if (event.key === 'ArrowDown' || event.key === 'ArrowRight') {
      nextIndex = (currentIndex + 1) % viewOptions.length;
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') {
      nextIndex = (currentIndex - 1 + viewOptions.length) % viewOptions.length;
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = viewOptions.length - 1;
    }

    if (nextIndex == null) return;
    event.preventDefault();
    const nextOption = viewOptions[nextIndex];
    onAlertsViewChange(nextOption.id);
    tabRefs.current[nextIndex]?.focus();
  };

  return (
    <div
      data-testid="alerts-controls"
      className="flex flex-col px-3 pt-2 pb-3 gap-3"
    >
      <div
        role="tablist"
        aria-label="Alerts sub-views"
        aria-orientation="vertical"
        className={`flex flex-col gap-2 rounded-lg p-1 border ${
          isDark ? 'bg-neutral-950 border-neutral-700' : 'bg-gray-100 border-gray-200'
        }`}
      >
        {viewOptions.map((option, index) => {
          const isSelected = alertsView === option.id;
          return (
            <button
              key={option.id}
              ref={(el) => {
                tabRefs.current[index] = el;
              }}
              id={tabId(option.id)}
              role="tab"
              aria-selected={isSelected}
              aria-controls={ALERTS_VIEW_PANEL_ID[option.id]}
              tabIndex={isSelected ? 0 : -1}
              data-testid={`alerts-view-${option.id}`}
              onClick={() => onAlertsViewChange(option.id)}
              onKeyDown={(event) => handleTabKeyDown(event, index)}
              className={`flex items-center gap-2 px-3 py-2 rounded text-sm text-left transition-colors ${
                isSelected
                  ? 'bg-neutral-300 dark:bg-neutral-700 text-neutral-900 dark:text-white font-medium ring-1 ring-[#76b900]'
                  : isDark
                  ? 'text-neutral-300 hover:bg-neutral-800'
                  : 'text-neutral-700 hover:bg-neutral-200'
              }`}
            >
              <span className={`flex-shrink-0 ${isSelected ? 'text-[#76b900]' : ''}`}>
                {option.icon}
              </span>
              <span>{option.label}</span>
            </button>
          );
        })}
      </div>
      <div className={`h-px w-full ${isDark ? 'bg-neutral-700' : 'bg-gray-200'}`} />

      {manageAlertsEnabled && alertsView === 'create' && (
        <div className="mt-1 flex flex-col gap-3">
          {createActiveKind === 'real-time' && (
            <>
              <div className="flex flex-col gap-1">
                <label
                  htmlFor="filter-stream-url"
                  className={`text-sm font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`}
                >
                  Live Stream URL
                </label>
                <input
                  id="filter-stream-url"
                  data-testid="filter-stream-url"
                  type="text"
                  placeholder="Filter by URL"
                  value={streamFilter}
                  onChange={(e) => onStreamFilterChange(e.target.value)}
                  className={`w-full rounded-md px-3 py-2 text-sm focus:outline-none transition-colors ${
                    isDark
                      ? 'bg-neutral-900 border border-neutral-700 text-neutral-100 placeholder-neutral-500 focus:border-[#76b900] focus:ring-1 focus:ring-[#76b900]/40'
                      : 'bg-white border border-gray-300 text-gray-800 placeholder-gray-400 focus:border-green-500 focus:ring-1 focus:ring-green-200'
                  }`}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label
                  htmlFor="filter-alert-type-rt"
                  className={`text-sm font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`}
                >
                  Alert Type
                </label>
                <input
                  id="filter-alert-type-rt"
                  data-testid="filter-alert-type-rt"
                  type="text"
                  placeholder="Filter by type"
                  value={typeFilter}
                  onChange={(e) => onTypeFilterChange(e.target.value)}
                  className={`w-full rounded-md px-3 py-2 text-sm focus:outline-none transition-colors ${
                    isDark
                      ? 'bg-neutral-900 border border-neutral-700 text-neutral-100 placeholder-neutral-500 focus:border-[#76b900] focus:ring-1 focus:ring-[#76b900]/40'
                      : 'bg-white border border-gray-300 text-gray-800 placeholder-gray-400 focus:border-green-500 focus:ring-1 focus:ring-green-200'
                  }`}
                />
              </div>
            </>
          )}
          <button
            type="button"
            onClick={onAddNewAlertRule}
            data-testid="alerts-controls-add-new"
            className={`flex items-center justify-center gap-2 px-3 py-2 rounded-md border text-sm font-medium transition-colors ${
              isDark
                ? 'border-neutral-700 bg-neutral-900 text-neutral-100 hover:bg-neutral-800 hover:border-[#76b900]'
                : 'border-gray-300 bg-white text-gray-800 hover:bg-gray-100 hover:border-green-500'
            }`}
          >
            <IconPlus size={16} />
            Create alert rule
          </button>
        </div>
      )}

      {alertsView === 'view' && (
        <div className="flex flex-col gap-3">
          <AlertsViewFilterControls
            isDark={isDark}
            vlmVerified={vlmVerified}
            vlmVerdict={vlmVerdict}
            uniqueValues={uniqueValues}
            onVlmVerifiedChange={onVlmVerifiedChange}
            onVlmVerdictChange={onVlmVerdictChange}
            onAddFilter={onAddFilter}
            activeFilters={activeFilters}
            onRemoveFilter={onRemoveFilter}
            onClearAllFilters={onClearAllFilters}
            timeWindow={timeWindow}
            showCustomTimeInput={showCustomTimeInput}
            customTimeValue={customTimeValue}
            customTimeError={customTimeError}
            maxTimeLimitInMinutes={maxTimeLimitInMinutes}
            onTimeWindowChange={onTimeWindowChange}
            onCustomTimeValueChange={onCustomTimeValueChange}
            onCustomTimeApply={onCustomTimeApply}
            onCustomTimeCancel={onCustomTimeCancel}
            onOpenCustomTime={onOpenCustomTime}
            fetchSize={fetchSize}
            onFetchSizeChange={onFetchSizeChange}
          />
        </div>
      )}
    </div>
  );
};
