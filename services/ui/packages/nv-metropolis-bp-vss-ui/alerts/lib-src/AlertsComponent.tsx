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
  VlmVerdict,
  VLM_VERDICT,
  isValidVlmVerdict,
  AlertsView,
  AlertRulesType,
} from './types';
import { useAlerts } from './hooks/useAlerts';
import { useFilters } from './hooks/useFilters';
import { useTimeWindow } from './hooks/useTimeWindow';
import { useAutoRefresh } from './hooks/useAutoRefresh';
import { useSessionState, parseIntRange } from './hooks/useSessionState';
import { useSessionFilterState } from './hooks/useSessionFilterState';
import { AlertsTable } from './components/AlertsTable';
import { AlertsSidebarControls, ALERTS_VIEW_PANEL_ID } from './components/AlertsSidebarControls';
import { CreateAlertRulesView, triggerRealtimeAddDraft } from './components/CreateAlertRulesView';
import { triggerVerificationAddDraft } from './components/CvAlertsVerificationTab';

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
  const enableRealtimeAlerts = alertsData?.enableRealtimeAlerts ?? true;
  const enableCvAlertsVerification = alertsData?.enableCvAlertsVerification ?? true;
  const manageAlertsEnabled = enableRealtimeAlerts || enableCvAlertsVerification;
  const defaultAlertRulesKind: AlertRulesType = enableRealtimeAlerts
    ? 'real-time'
    : 'verification';

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
    (value) => (manageAlertsEnabled && value === 'create' ? 'create' : 'view'),
  );
  const [alertRulesKind, setAlertRulesKind] = useSessionPersistedState<AlertRulesType>(
    'alertsTabRulesKind',
    defaultAlertRulesKind,
    (value) => {
      if (value === 'real-time' && enableRealtimeAlerts) return 'real-time';
      if (value === 'verification' && enableCvAlertsVerification) return 'verification';
      return defaultAlertRulesKind;
    },
  );

  // Mount Manage Alerts once the user opens it; keep it mounted afterward so RTSP
  // thumbnails and draft rows are not torn down on sub-tab switches. Read
  // sessionStorage synchronously here (unlike `alertsView`) so a restored Manage
  // Alerts session mounts on the first client paint without waiting for deferred
  // hydration — the panel stays `display:none` until `alertsView` catches up.
  const [hasOpenedManageAlerts, setHasOpenedManageAlerts] = React.useState(() => {
    if (!manageAlertsEnabled) return false;
    if (typeof window === 'undefined') return false;
    try {
      const raw = sessionStorage.getItem('alertsTabView');
      if (raw == null) return false;
      return (JSON.parse(raw) as AlertsView) === 'create';
    } catch {
      return false;
    }
  });
  React.useEffect(() => {
    if (alertsView === 'create') {
      setHasOpenedManageAlerts(true);
    }
  }, [alertsView]);

  // Switch into Manage Alerts and append a draft in the selected kind tab
  // (Real-time Alerts or CV Alerts Verification). Each editor owns its draft list, so we
  // delegate via module-level bridges. If the tab hasn't mounted yet (sidebar
  // click from View mode), retry on subsequent animation frames until the
  // bridge is wired up — bounded so we don't spin forever if
  // CreateAlertRulesView fails to mount for some reason.
  const handleAddNewAlertRule = React.useCallback(() => {
    if (!manageAlertsEnabled) return;
    setAlertsView('create');
    const triggerAddDraft =
      alertRulesKind === 'verification'
        ? triggerVerificationAddDraft
        : triggerRealtimeAddDraft;
    if (triggerAddDraft()) return;

    const MAX_ATTEMPTS = 10;
    let attempts = 0;
    let rafId = 0;
    const tick = () => {
      if (triggerAddDraft()) return;
      attempts += 1;
      if (attempts >= MAX_ATTEMPTS) return;
      rafId = requestAnimationFrame(tick);
    };
    rafId = requestAnimationFrame(tick);
    // The retry chain self-terminates on success or after MAX_ATTEMPTS;
    // `rafId` is captured here only so a future caller can cancel if needed.
    void rafId;
  }, [alertRulesKind, manageAlertsEnabled, setAlertsView]);

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
  const vlmVerifiedAlertReportPromptTemplate = alertsData?.vlmVerifiedAlertReportPromptTemplate;
  const mediaWithObjectsBbox = alertsData?.mediaWithObjectsBbox ?? false;

  const [pageSize, setPageSize] = useSessionState('alertsTabPageSize', defaultPageSize, parseIntRange(1, 500));
  const [maxResults, setMaxResults] = useSessionState('alertsTabMaxResults', defaultMaxResults, parseIntRange(10, 5000));

  const [activeFilters, setActiveFilters] = useSessionFilterState(FILTERS_STORAGE_KEY);
  const [streamFilter, setStreamFilter] = React.useState('');
  const [typeFilter, setTypeFilter] = React.useState('');

  // Maintain separate alertTypes & alertTriggered filter selections per
  // vlmVerified state. When the toggle flips, the current selections are saved
  // into the old bucket and the previously saved selections for the new state
  // are restored, so tags never leak between the two states.
  // Done via a synchronous callback (not an effect) to avoid an intermediate
  // render frame where stale tags from the wrong state are briefly visible.
  const vlmFiltersRef = React.useRef<{
    enabled: { alertTypes: Set<string>; alertTriggered: Set<string> };
    disabled: { alertTypes: Set<string>; alertTriggered: Set<string> };
  }>({
    enabled: { alertTypes: new Set(), alertTriggered: new Set() },
    disabled: { alertTypes: new Set(), alertTriggered: new Set() },
  });
  const handleVlmVerifiedChange = React.useCallback((next: boolean) => {
    setActiveFilters(prev => {
      const curBucket = vlmVerified
        ? vlmFiltersRef.current.enabled
        : vlmFiltersRef.current.disabled;
      curBucket.alertTypes = new Set(prev.alertTypes);
      curBucket.alertTriggered = new Set(prev.alertTriggered);

      const newBucket = next
        ? vlmFiltersRef.current.enabled
        : vlmFiltersRef.current.disabled;
      return {
        ...prev,
        alertTypes: new Set(newBucket.alertTypes),
        alertTriggered: new Set(newBucket.alertTriggered),
      };
    });
    setVlmVerified(next);
  }, [vlmVerified, setActiveFilters, setVlmVerified]);

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
    vlmVerified,
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

  const handleClearAllFilters = React.useCallback(() => {
    setActiveFilters({
      sensors: new Set<string>(),
      alertTypes: new Set<string>(),
      alertTriggered: new Set<string>(),
    });
  }, [setActiveFilters]);

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
      <AlertsSidebarControls
        isDark={isDark}
        alertsView={alertsView}
        onAlertsViewChange={setAlertsView}
        onAddNewAlertRule={handleAddNewAlertRule}
        manageAlertsEnabled={manageAlertsEnabled}
        vlmVerified={vlmVerified}
        vlmVerdict={vlmVerdict}
        uniqueValues={uniqueValues}
        onVlmVerifiedChange={handleVlmVerifiedChange}
        onVlmVerdictChange={setVlmVerdict}
        onAddFilter={addFilter}
        activeFilters={activeFilters}
        onRemoveFilter={removeFilter}
        onClearAllFilters={handleClearAllFilters}
        timeWindow={timeWindow}
        showCustomTimeInput={showCustomTimeInput}
        customTimeValue={customTimeValue}
        customTimeError={customTimeError}
        maxTimeLimitInMinutes={maxTimeLimitInMinutes}
        onTimeWindowChange={setTimeWindow}
        onCustomTimeValueChange={handleCustomTimeChange}
        onCustomTimeApply={handleSetCustomTime}
        onCustomTimeCancel={handleCancelCustomTime}
        onOpenCustomTime={openCustomTimeInput}
        fetchSize={maxResults}
        onFetchSizeChange={setMaxResults}
        createActiveKind={alertRulesKind}
        streamFilter={streamFilter}
        typeFilter={typeFilter}
        onStreamFilterChange={setStreamFilter}
        onTypeFilterChange={setTypeFilter}
        loading={loading}
        autoRefreshEnabled={autoRefreshEnabled}
        autoRefreshInterval={autoRefreshInterval}
        onRefresh={refetch}
        onAutoRefreshToggle={toggleAutoRefresh}
        onAutoRefreshIntervalChange={setAutoRefreshInterval}
      />
    ),
    [
      isDark,
      alertsView,
      handleAddNewAlertRule,
      manageAlertsEnabled,
      vlmVerified,
      vlmVerdict,
      uniqueValues,
      handleVlmVerifiedChange,
      setVlmVerdict,
      addFilter,
      activeFilters,
      removeFilter,
      handleClearAllFilters,
      timeWindow,
      showCustomTimeInput,
      customTimeValue,
      customTimeError,
      maxTimeLimitInMinutes,
      setTimeWindow,
      handleCustomTimeChange,
      handleSetCustomTime,
      handleCancelCustomTime,
      openCustomTimeInput,
      maxResults,
      setMaxResults,
      alertRulesKind,
      streamFilter,
      typeFilter,
      loading,
      autoRefreshEnabled,
      autoRefreshInterval,
      refetch,
      toggleAutoRefresh,
      setAutoRefreshInterval,
    ],
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
        onVlmVerifiedChange: handleVlmVerifiedChange,
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
    handleVlmVerifiedChange,
    setTimeWindow,
    setAlertsView,
    controlsComponent,
  ]);

  const panelShellClass = `flex flex-col flex-1 min-h-0 min-w-0 ${
    isDark ? 'bg-black text-neutral-100' : 'bg-gray-50 text-gray-900'
  }`;

  return (
    <div
      data-testid="alerts-component"
      className={`flex flex-col h-full max-h-full ${
        isDark ? 'bg-black text-neutral-100' : 'bg-gray-50 text-gray-900'
      }`}
    >
      {/* Only rendered when the parent app is not hosting these controls in its
          left sidebar, so view tabs, filters and settings exist exactly once. */}
      {!renderControlsInLeftSidebar && (
        <div
          className={`flex-shrink-0 border-b ${isDark ? 'bg-black border-neutral-700' : 'bg-white border-gray-200'}`}
        >
          {controlsComponent}
        </div>
      )}

      {/* Keep both sub-views mounted (display:none when hidden) so Manage Alerts
          RTSP thumbnails and draft state are not torn down on tab switches. */}
      {manageAlertsEnabled && hasOpenedManageAlerts && (
        <div
          id={ALERTS_VIEW_PANEL_ID.create}
          role="tabpanel"
          aria-labelledby="alerts-tab-create"
          hidden={alertsView !== 'create'}
          className={panelShellClass}
          style={{ display: alertsView === 'create' ? 'flex' : 'none' }}
        >
          <CreateAlertRulesView
            isDark={isDark}
            activeKind={alertRulesKind}
            onActiveKindChange={setAlertRulesKind}
            onAddNew={handleAddNewAlertRule}
            streamFilter={streamFilter}
            typeFilter={typeFilter}
            onStreamFilterChange={setStreamFilter}
            onTypeFilterChange={setTypeFilter}
            alertsApiUrl={alertsApiUrl}
            vstApiUrl={vstApiUrl}
            enableRealtimeAlerts={enableRealtimeAlerts}
            enableCvAlertsVerification={enableCvAlertsVerification}
          />
        </div>
      )}

      <div
        id={ALERTS_VIEW_PANEL_ID.view}
        role="tabpanel"
        aria-labelledby="alerts-tab-view"
        hidden={alertsView !== 'view'}
        className={panelShellClass}
        style={{ display: alertsView === 'view' ? 'flex' : 'none' }}
      >
      {/* Alerts Table */}
      <div className="flex-1 min-h-0 overflow-hidden">
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
          vlmVerifiedAlertReportPromptTemplate={vlmVerifiedAlertReportPromptTemplate}
          vlmVerified={vlmVerified}
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
    </div>
  );
};

// Re-export types for convenience
export type { AlertData, AlertsComponentProps } from './types';
