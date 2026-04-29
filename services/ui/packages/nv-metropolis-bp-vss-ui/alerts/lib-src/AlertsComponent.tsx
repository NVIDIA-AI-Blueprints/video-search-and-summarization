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

import {
  AlertsComponentProps,
  FilterType,
  VlmVerdict,
  VLM_VERDICT,
  isValidVlmVerdict,
  AlertsView,
} from './types';
import { useAlerts } from './hooks/useAlerts';
import { useFilters } from './hooks/useFilters';
import { useTimeWindow } from './hooks/useTimeWindow';
import { useAutoRefresh } from './hooks/useAutoRefresh';
import { useSessionState, parseIntRange } from './hooks/useSessionState';
import { useSessionFilterState } from './hooks/useSessionFilterState';
import { FilterTag } from './components/FilterTag';
import { AlertsTable } from './components/AlertsTable';
import { FilterControls } from './components/FilterControls';
import { Controls, ALERTS_VIEW_PANEL_ID } from './components/Controls';
import { CreateAlertRulesView, triggerRealtimeAddDraft } from './components/CreateAlertRulesView';

const readSessionState = <T,>(key: string, fallback: T): T => {
  if (typeof window === 'undefined') return fallback;
  try {
    const raw = sessionStorage.getItem(key);
    if (raw == null) return fallback;
    return JSON.parse(raw) as T;
  } catch (error) {
    console.warn(`Failed to load ${key} from sessionStorage:`, error);
    return fallback;
  }
};

/**
 * SSR-safe sessionStorage-backed useState. The state is initialized to the
 * provided default (so server and client first render match) and then hydrated
 * from sessionStorage in a one-shot effect after mount. This avoids React's
 * "Expected server HTML to contain a matching ..." hydration warnings when
 * the persisted value differs from the default.
 */
function useSessionPersistedState<T>(
  key: string,
  defaultValue: T,
  transform: (raw: T) => T = (raw) => raw,
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [value, setValue] = React.useState<T>(defaultValue);
  // `hydrated` must be state (not a ref) so flipping it triggers a re-render
  // *after* the hydration effect's setValue has been applied. The persist
  // effect then runs with the actual stored value rather than the still-stale
  // default — otherwise it would write the default back to storage on mount.
  const [hydrated, setHydrated] = React.useState(false);

  React.useEffect(() => {
    const stored = readSessionState<T | null>(key, null);
    if (stored !== null) {
      setValue(transform(stored));
    }
    setHydrated(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  React.useEffect(() => {
    if (!hydrated) return;
    if (typeof window === 'undefined') return;
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      console.warn(`Failed to save ${key} to sessionStorage:`, error);
    }
  }, [key, value, hydrated]);

  return [value, setValue];
}

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

  // Primitive session-persisted state. `useSessionState` reads sessionStorage
  // synchronously in the useState initializer; React 18 patches the DOM if the
  // stored value differs from the SSR default.
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

  // Alerts sub-view (View Alerts vs Manage Alerts). Uses deferred hydration
  // because this drives a top-level branch in the JSX — reading sessionStorage
  // synchronously would produce different SSR vs first-client trees and trip
  // React's hydration mismatch warning.
  const [alertsView, setAlertsView] = useSessionPersistedState<AlertsView>(
    'alertsTabView',
    'view',
  );

  // Switch into the create view and append a new draft row. The realtime tab
  // owns its draft list, so we delegate via a module-level bridge. If the tab
  // hasn't mounted yet (sidebar click from View mode), retry on subsequent
  // animation frames until the bridge is wired up — bounded so we don't spin
  // forever if CreateAlertRulesView fails to mount for some reason.
  const handleAddNewAlertRule = React.useCallback(() => {
    setAlertsView('create');
    if (triggerRealtimeAddDraft()) return;

    const MAX_ATTEMPTS = 10;
    let attempts = 0;
    let rafId = 0;
    const tick = () => {
      if (triggerRealtimeAddDraft()) return;
      attempts += 1;
      if (attempts >= MAX_ATTEMPTS) return;
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    // The retry chain self-terminates on success or after MAX_ATTEMPTS;
    // `rafId` is captured here only so a future caller can cancel if needed.
    void rafId;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  const apiUrl = alertsData?.apiUrl;
  const vstApiUrl = alertsData?.vstApiUrl;
  const alertsApiUrl = alertsData?.alertsApiUrl;
  const defaultMaxResults = alertsData?.maxResults ?? 100;
  const defaultPageSize = alertsData?.pageSize ?? 20;
  const alertReportPromptTemplate = alertsData?.alertReportPromptTemplate;
  const mediaWithObjectsBbox = alertsData?.mediaWithObjectsBbox ?? false;

  const [pageSize, setPageSize] = useSessionState('alertsTabPageSize', defaultPageSize, parseIntRange(1, 500));
  const [maxResults, setMaxResults] = useSessionState('alertsTabMaxResults', defaultMaxResults, parseIntRange(10, 5000));

  const [activeFilters, setActiveFilters] = useSessionFilterState(FILTERS_STORAGE_KEY);

  /** Incremented when "Show more" succeeds so AlertsTable resets column sort but keeps current page. */
  const [loadMoreCompletionCount, setLoadMoreCompletionCount] = React.useState(0);

  // `activeFilters` is forwarded to useAlerts for server-side queryString filtering.
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
  
  // `useFilters` is driven by external state; sensors dropdown reads from the
  // API-provided sensorList rather than accumulating from data.
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

  const controlsComponent = React.useMemo(
    () => (
      <Controls
        isDark={isDark}
        alertsView={alertsView}
        onAlertsViewChange={setAlertsView}
        onAddNewAlertRule={handleAddNewAlertRule}
      />
    ),
    [isDark, alertsView, setAlertsView, handleAddNewAlertRule],
  );

  // Push control handlers to the parent whenever relevant state changes so
  // the externally-rendered sidebar stays in sync with this component.
  useEffect(() => {
    if (onControlsReady && renderControlsInLeftSidebar) {
      onControlsReady({
        isDark,
        vlmVerified,
        timeWindow,
        autoRefreshEnabled,
        autoRefreshInterval,
        refreshControlsSuspended: false,
        alertsView,
        onVlmVerifiedChange: setVlmVerified,
        onTimeWindowChange: setTimeWindow,
        onRefresh: refetch,
        onAutoRefreshToggle: toggleAutoRefresh,
        onAlertsViewChange: setAlertsView,
        onAddNewAlertRule: handleAddNewAlertRule,
        controlsComponent,
      });
    }
  }, [
    onControlsReady,
    renderControlsInLeftSidebar,
    isDark,
    vlmVerified,
    timeWindow,
    autoRefreshEnabled,
    autoRefreshInterval,
    alertsView,
    refetch,
    toggleAutoRefresh,
    handleAddNewAlertRule,
    setVlmVerified,
    setTimeWindow,
    setAlertsView,
    controlsComponent,
  ]);

  if (alertsView === 'create') {
    return (
      <div
        data-testid="alerts-component"
        id={ALERTS_VIEW_PANEL_ID.create}
        role="tabpanel"
        aria-labelledby="alerts-tab-create"
        className={`flex flex-col h-full max-h-full ${isDark ? 'bg-black text-neutral-100' : 'bg-gray-50 text-gray-900'}`}
      >
        <CreateAlertRulesView
          isDark={isDark}
          activeKind="real-time"
          onAddNew={handleAddNewAlertRule}
          alertsApiUrl={alertsApiUrl}
          vstApiUrl={vstApiUrl}
        />
      </div>
    );
  }

  return (
    <div 
      data-testid="alerts-component"
      id={ALERTS_VIEW_PANEL_ID.view}
      role="tabpanel"
      aria-labelledby="alerts-tab-view"
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
