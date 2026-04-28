// SPDX-License-Identifier: MIT
/**
 * Main Alerts Management Component
 * 
 * This is the primary component for the alerts management system, providing
 * a comprehensive interface for viewing, filtering, and managing security
 * and monitoring alerts with advanced time-based filtering capabilities.
 * 
 */

import React, { useEffect } from 'react';
import { VideoModal, useVideoModal } from '@nemo-agent-toolkit/ui';

// Types
import { AlertsComponentProps, FilterType, VlmVerdict, VLM_VERDICT, isValidVlmVerdict } from './types';

// Hooks
import { useAlerts } from './hooks/useAlerts';
import { useFilters } from './hooks/useFilters';
import { useTimeWindow } from './hooks/useTimeWindow';
import { useAutoRefresh } from './hooks/useAutoRefresh';
import { useSessionState, parseIntRange } from './hooks/useSessionState';
import { useSessionFilterState } from './hooks/useSessionFilterState';

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

const FILTERS_STORAGE_KEY = 'alertsTabActiveFilters';

export const AlertsComponent: React.FC<AlertsComponentProps> = ({
  theme = 'light',
  onThemeChange,
  isActive = true,
  alertsData,
  serverRenderTime,
  renderControlsInLeftSidebar = false,
  onControlsReady,
  submitChatMessage,
}) => {
  const isDark = theme === 'dark';
  
  // Session-persisted UI states (reads from sessionStorage in useState initializer)
  const [vlmVerified, setVlmVerified] = useSessionState<boolean>(
    'alertsTabVlmVerified', alertsData?.defaultVlmVerified ?? true,
    (s) => s === 'true' ? true : s === 'false' ? false : null,
  );
  const [vlmVerdict, setVlmVerdict] = useSessionState<VlmVerdict>(
    'alertsTabVlmVerdict', VLM_VERDICT.ALL,
    (s) => isValidVlmVerdict(s) ? s : null,
  );
  const [timeFormat, setTimeFormat] = useSessionState<'local' | 'utc'>(
    'alertsTabTimeFormat', 'local',
    (s) => s === 'local' || s === 'utc' ? s : null,
  );
  
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
  const defaultMaxResults = alertsData?.maxResults ?? 100;
  const defaultPageSize = alertsData?.pageSize ?? 20;
  const alertReportPromptTemplate = alertsData?.alertReportPromptTemplate;
  const mediaWithObjectsBbox = alertsData?.mediaWithObjectsBbox ?? false;

  const [pageSize, setPageSize] = useSessionState('alertsTabPageSize', defaultPageSize, parseIntRange(1, 500));
  const [maxResults, setMaxResults] = useSessionState('alertsTabMaxResults', defaultMaxResults, parseIntRange(10, 5000));

  // Active filters state - persisted to sessionStorage so filters survive refreshes.
  const [activeFilters, setActiveFilters] = useSessionFilterState(FILTERS_STORAGE_KEY);

  /** Incremented when "Show more" succeeds so AlertsTable resets column sort but keeps current page. */
  const [loadMoreCompletionCount, setLoadMoreCompletionCount] = React.useState(0);

  // Custom hooks for data and functionality
  // Pass activeFilters to useAlerts for server-side filtering via queryString
  const {
    alerts,
    loading,
    loadingMore,
    error,
    sensorMap,
    sensorList,
    refetch,
    loadMoreAlerts,
    canLoadMore,
  } = useAlerts({
    apiUrl,
    vstApiUrl,
    vlmVerified,
    vlmVerdict,
    timeWindow,
    maxResults,
    activeFilters,
  });

  // Refetch data (including sensor list) when tab transitions from inactive → active.
  // Only react to `isActive` changes; `refetch` is deliberately excluded to avoid
  // duplicate API calls (useAlerts already refetches when its deps change).
  const prevIsActiveRef = React.useRef(isActive);
  useEffect(() => {
    const wasActive = prevIsActiveRef.current;
    prevIsActiveRef.current = isActive;

    if (isActive && !wasActive) {
      refetch({ includeSensorList: true });
    }
  }, [isActive]); // eslint-disable-line react-hooks/exhaustive-deps
  
  // useFilters now uses external state management
  // sensorList from API is used for sensors dropdown instead of accumulating from data
  const { addFilter, removeFilter, filteredAlerts, uniqueValues } = useFilters({
    alerts,
    externalFilters: activeFilters,
    onFiltersChange: setActiveFilters,
    sensorList
  });

  const paginationResetKey = React.useMemo(
    () =>
      JSON.stringify({
        tw: timeWindow,
        vv: vlmVerified,
        vx: vlmVerdict,
        ps: pageSize,
        s: [...activeFilters.sensors].sort((a, b) => a.localeCompare(b)),
        a: [...activeFilters.alertTypes].sort((a, b) => a.localeCompare(b)),
        t: [...activeFilters.alertTriggered].sort((a, b) => a.localeCompare(b)),
      }),
    [timeWindow, vlmVerified, vlmVerdict, pageSize, activeFilters],
  );
  const { videoModal, openVideoModalFromAlert, closeVideoModal, loadingAlertId } = useVideoModal(vstApiUrl, { sensorMap, showObjectsBbox: mediaWithObjectsBbox });

  const handleTableLoadMore = React.useCallback(async () => {
    const ok = await loadMoreAlerts();
    if (ok) {
      setLoadMoreCompletionCount((c) => c + 1);
    }
  }, [loadMoreAlerts]);
  
  // Auto-refresh management: paused on client-side table pages 2+ so paging is stable; resumes on page 1.
  // Tab visibility is unchanged (isActive prop on AlertsComponent is separate from this).
  const {
    isEnabled: autoRefreshEnabled,
    interval: autoRefreshInterval,
    setInterval: setAutoRefreshInterval,
    toggleEnabled: toggleAutoRefresh
  } = useAutoRefresh({
    defaultInterval: alertsData?.defaultAutoRefreshInterval || 1000,
    onRefresh: refetch,
    enabled: true,
    isActive: true,
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
        refreshControlsSuspended: false,
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
    controlsComponent,
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
          onCustomTimeValueChange={handleCustomTimeChange}
          onCustomTimeApply={handleSetCustomTime}
          onCustomTimeCancel={handleCancelCustomTime}
          onOpenCustomTime={openCustomTimeInput}
          onAddFilter={addFilter}
          onRefresh={refetch}
          onAutoRefreshToggle={toggleAutoRefresh}
          onAutoRefreshIntervalChange={setAutoRefreshInterval}
          fetchSize={maxResults}
          onFetchSizeChange={setMaxResults}
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
          onTimeFormatChange={setTimeFormat}
          pageSize={pageSize}
          onPageSizeChange={setPageSize}
          paginationResetKey={paginationResetKey}
          loadMoreBatchSize={maxResults}
          canLoadMore={canLoadMore}
          loadingMore={loadingMore}
          onLoadMore={handleTableLoadMore}
          loadMoreCompletionCount={loadMoreCompletionCount}
          autoRefreshEnabled={autoRefreshEnabled}
          submitChatMessage={submitChatMessage}
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
