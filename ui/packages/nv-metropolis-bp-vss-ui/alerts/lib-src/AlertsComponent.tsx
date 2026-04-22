// SPDX-License-Identifier: MIT
/**
 * Main Alerts Management Component
 * 
 * This is the primary component for the alerts management system, providing
 * a comprehensive interface for viewing, filtering, and managing security
 * and monitoring alerts with advanced time-based filtering capabilities.
 * 
 */

import React, { useState, useEffect } from 'react';
import { VideoModal, useVideoModal } from '@nemo-agent-toolkit/ui';

// Types
import { AlertsComponentProps, FilterType, FilterState, VlmVerdict, VLM_VERDICT } from './types';

// Hooks
import { useAlerts } from './hooks/useAlerts';
import { useFilters, createEmptyFilterState } from './hooks/useFilters';
import { useTimeWindow } from './hooks/useTimeWindow';
import { useAutoRefresh } from './hooks/useAutoRefresh';

// Components
import { FilterTag } from './components/FilterTag';
import { AlertsTable } from './components/AlertsTable';
import { FilterControls } from './components/FilterControls';
import { AlertsSidebarControls } from './components/AlertsSidebarControls';

/**
 * Filter colors configuration - moved outside component to avoid recreation on every render
 */
const FILTER_COLORS = {
  sensors: {
    dark: { bg: 'bg-transparent', border: 'border border-green-500', text: 'text-green-400', hover: 'hover:text-green-300' },
    light: { bg: 'bg-green-100', border: 'border border-green-300', text: 'text-green-700', hover: 'hover:text-green-900' }
  },
  alertTypes: {
    dark: { bg: 'bg-transparent', border: 'border border-orange-500', text: 'text-orange-400', hover: 'hover:text-orange-300' },
    light: { bg: 'bg-purple-100', border: 'border border-purple-300', text: 'text-purple-700', hover: 'hover:text-purple-900' }
  },
  alertTriggered: {
    dark: { bg: 'bg-transparent', border: 'border border-emerald-500', text: 'text-emerald-400', hover: 'hover:text-emerald-300' },
    light: { bg: 'bg-emerald-100', border: 'border border-emerald-300', text: 'text-emerald-700', hover: 'hover:text-emerald-900' }
  }
} as const;

const getFilterColors = (type: FilterType, isDark: boolean) => {
  return FILTER_COLORS[type][isDark ? 'dark' : 'light'];
};

export const AlertsComponent: React.FC<AlertsComponentProps> = ({
  theme = 'light',
  onThemeChange,
  isActive = true,
  alertsData,
  serverRenderTime,
  renderControlsInLeftSidebar = false,
  onControlsReady
}) => {
  const isDark = theme === 'dark';
  
  // VLM Verified state - persist in sessionStorage
  const [vlmVerified, setVlmVerified] = useState<boolean>(() => {
    // Try to load from sessionStorage first, fallback to default
    if (typeof window !== 'undefined') {
      try {
        const stored = sessionStorage.getItem('alertsTabVlmVerified');
        if (stored != null) {
          return JSON.parse(stored);
        }
      } catch (error) {
        console.warn('Failed to load vlmVerified from sessionStorage:', error);
      }
    }
    return alertsData?.defaultVlmVerified ?? true;
  });

  // Save vlmVerified to sessionStorage whenever it changes
  useEffect(() => {
    if (typeof window !== 'undefined') {
      try {
        sessionStorage.setItem('alertsTabVlmVerified', JSON.stringify(vlmVerified));
      } catch (error) {
        console.warn('Failed to save vlmVerified to sessionStorage:', error);
      }
    }
  }, [vlmVerified]);

  // VLM Verdict state - persist in sessionStorage
  const [vlmVerdict, setVlmVerdict] = useState<VlmVerdict>(() => {
    // Try to load from sessionStorage first, fallback to default
    if (typeof window !== 'undefined') {
      try {
        const stored = sessionStorage.getItem('alertsTabVlmVerdict');
        if (stored !== null) {
          return stored as VlmVerdict;
        }
      } catch (error) {
        console.warn('Failed to load vlmVerdict from sessionStorage:', error);
      }
    }
    return VLM_VERDICT.ALL;
  });

  // Save vlmVerdict to sessionStorage whenever it changes
  useEffect(() => {
    if (typeof window !== 'undefined') {
      try {
        sessionStorage.setItem('alertsTabVlmVerdict', vlmVerdict);
      } catch (error) {
        console.warn('Failed to save vlmVerdict to sessionStorage:', error);
      }
    }
  }, [vlmVerdict]);

  // Time format (UTC vs Local) - persist in sessionStorage
  const [timeFormat, setTimeFormat] = useState<'local' | 'utc'>(() => {
    if (typeof window !== 'undefined') {
      try {
        const stored = sessionStorage.getItem('alertsTabTimeFormat');
        if (stored === 'utc' || stored === 'local') return stored;
      } catch (error) {
        console.warn('Failed to load timeFormat from sessionStorage:', error);
      }
    }
    return 'local';
  });

  useEffect(() => {
    if (typeof window !== 'undefined') {
      try {
        sessionStorage.setItem('alertsTabTimeFormat', timeFormat);
      } catch (error) {
        console.warn('Failed to save timeFormat to sessionStorage:', error);
      }
    }
  }, [timeFormat]);
  
  // Time window management
  const {
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
  } = useTimeWindow({ 
    defaultTimeWindow: alertsData?.defaultTimeWindow,
    maxSearchTimeLimit: alertsData?.maxSearchTimeLimit
  });

  // Extract API URLs and config from alertsData
  const apiUrl = alertsData?.apiUrl;
  const vstApiUrl = alertsData?.vstApiUrl;
  const maxResults = alertsData?.maxResults;
  const alertReportPromptTemplate = alertsData?.alertReportPromptTemplate;
  const mediaWithObjectsBbox = alertsData?.mediaWithObjectsBbox ?? false;

  // Active filters state - managed at component level for server-side filtering
  const [activeFilters, setActiveFilters] = useState<FilterState>(createEmptyFilterState);

  // Custom hooks for data and functionality
  // Pass activeFilters to useAlerts for server-side filtering via queryString
  const { alerts, loading, error, sensorMap, sensorList, refetch } = useAlerts({ 
    apiUrl, 
    vstApiUrl, 
    vlmVerified,
    vlmVerdict,
    timeWindow,
    maxResults,
    activeFilters
  });

  // Refetch data (including sensor list) when tab becomes active
  // This ensures fresh data when user navigates to the Alerts tab
  const isFirstRender = React.useRef(true);
  useEffect(() => {
    // Skip first render (initial mount already fetches data)
    if (isFirstRender.current) {
      isFirstRender.current = false;
      return;
    }
    
    // When tab becomes active, refetch everything including sensor list
    if (isActive) {
      refetch({ includeSensorList: true });
    }
  }, [isActive, refetch]);
  
  // useFilters now uses external state management
  // sensorList from API is used for sensors dropdown instead of accumulating from data
  const { addFilter, removeFilter, filteredAlerts, uniqueValues } = useFilters({
    alerts,
    externalFilters: activeFilters,
    onFiltersChange: setActiveFilters,
    sensorList
  });
  const { videoModal, openVideoModalFromAlert, closeVideoModal, loadingAlertId } = useVideoModal(vstApiUrl, { sensorMap, showObjectsBbox: mediaWithObjectsBbox });
  
  // Auto-refresh management
  // Note: autorefresh continues even when tab is hidden (similar to Kibana behavior)
  const {
    isEnabled: autoRefreshEnabled,
    interval: autoRefreshInterval,
    setInterval: setAutoRefreshInterval,
    toggleEnabled: toggleAutoRefresh
  } = useAutoRefresh({
    defaultInterval: alertsData?.defaultAutoRefreshInterval || 1000,
    onRefresh: refetch,
    enabled: true
    // isActive not passed - defaults to true, so autorefresh always runs
  });

  // Memoize the controls component to prevent unnecessary re-renders
  const controlsComponent = React.useMemo(
    () => (
      <AlertsSidebarControls
        isDark={isDark}
        vlmVerified={vlmVerified}
        timeWindow={timeWindow}
        autoRefreshEnabled={autoRefreshEnabled}
        autoRefreshInterval={autoRefreshInterval}
        onVlmVerifiedChange={setVlmVerified}
        onTimeWindowChange={setTimeWindow}
        onRefresh={refetch}
        onAutoRefreshToggle={toggleAutoRefresh}
      />
    ),
    [
      isDark,
      vlmVerified,
      timeWindow,
      autoRefreshEnabled,
      autoRefreshInterval,
      setVlmVerified,
      setTimeWindow,
      refetch,
      toggleAutoRefresh,
    ]
  );

  // Provide control handlers to parent if external rendering is enabled
  useEffect(() => {
    if (onControlsReady && renderControlsInLeftSidebar) {
      onControlsReady({
        isDark,
        vlmVerified,
        timeWindow,
        autoRefreshEnabled,
        autoRefreshInterval,
        onVlmVerifiedChange: setVlmVerified,
        onTimeWindowChange: setTimeWindow,
        onRefresh: refetch,
        onAutoRefreshToggle: toggleAutoRefresh,
        controlsComponent,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    onControlsReady,
    renderControlsInLeftSidebar,
  ]);

  return (
    <div 
      data-testid="alerts-component"
      className={`flex flex-col h-full max-h-full ${isDark ? 'bg-black text-neutral-100' : 'bg-gray-50 text-gray-900'}`}
    >
      {/* Header with Filters */}
      <div className={`flex-shrink-0 px-6 py-4 border-b ${isDark ? 'bg-black border-neutral-700' : 'bg-white border-gray-200'}`}>
        {/* Filter Controls */}
        <FilterControls
          isDark={isDark}
          vlmVerified={vlmVerified}
          vlmVerdict={vlmVerdict}
          timeWindow={timeWindow}
          timeFormat={timeFormat}
          showCustomTimeInput={showCustomTimeInput}
          customTimeValue={customTimeValue}
          customTimeError={customTimeError}
          maxTimeLimitInMinutes={maxTimeLimitInMinutes}
          uniqueValues={uniqueValues}
          loading={loading}
          autoRefreshEnabled={autoRefreshEnabled}
          autoRefreshInterval={autoRefreshInterval}
          onVlmVerifiedChange={setVlmVerified}
          onVlmVerdictChange={setVlmVerdict}
          onTimeWindowChange={setTimeWindow}
          onTimeFormatChange={setTimeFormat}
          onCustomTimeValueChange={handleCustomTimeChange}
          onCustomTimeApply={handleSetCustomTime}
          onCustomTimeCancel={handleCancelCustomTime}
          onOpenCustomTime={openCustomTimeInput}
          onAddFilter={addFilter}
          onRefresh={refetch}
          onAutoRefreshToggle={toggleAutoRefresh}
          onAutoRefreshIntervalChange={setAutoRefreshInterval}
        />

        {/* Active Filter Tags */}
        {(activeFilters.sensors.size > 0 || activeFilters.alertTypes.size > 0 || activeFilters.alertTriggered.size > 0) && (
          <div className="flex items-center gap-2 flex-wrap mt-2">
            {Array.from(activeFilters.sensors).map(filter => (
              <FilterTag
                key={`sensor-${filter}`}
                type="sensors"
                filter={filter}
                colors={getFilterColors('sensors', isDark)}
                onRemove={removeFilter}
              />
            ))}

            {Array.from(activeFilters.alertTypes).map(filter => (
              <FilterTag
                key={`alertType-${filter}`}
                type="alertTypes"
                filter={filter}
                colors={getFilterColors('alertTypes', isDark)}
                onRemove={removeFilter}
              />
            ))}

            {Array.from(activeFilters.alertTriggered).map(filter => (
              <FilterTag
                key={`alertTriggered-${filter}`}
                type="alertTriggered"
                filter={filter}
                colors={getFilterColors('alertTriggered', isDark)}
                onRemove={removeFilter}
              />
            ))}
          </div>
        )}
      </div>

      {/* Alerts Table */}
      <div className="flex-1 overflow-auto">
        <AlertsTable
          alerts={filteredAlerts}
          loading={loading}
          error={error}
          isDark={isDark}
          activeFilters={activeFilters}
          onAddFilter={addFilter}
          onPlayVideo={openVideoModalFromAlert}
          loadingAlertId={loadingAlertId}
          onRefresh={refetch}
          alertReportPromptTemplate={alertReportPromptTemplate}
          vstApiUrl={vstApiUrl}
          sensorMap={sensorMap}
          showObjectsBbox={mediaWithObjectsBbox}
          timeFormat={timeFormat}
        />
      </div>

      {/* Video Modal */}
      <VideoModal
        isOpen={videoModal.isOpen}
        videoUrl={videoModal.videoUrl}
        title={videoModal.title}
        onClose={closeVideoModal}
      />
    </div>
  );
};

// Re-export types for convenience
export type { AlertData, AlertsComponentProps } from './types';
