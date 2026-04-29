// SPDX-License-Identifier: MIT
/**
 * CreateAlertRulesView - Real-time alert rule configuration editor.
 *
 * Lists rules from `GET /realtime`, creates them via `POST /realtime`,
 * and removes them with `DELETE /realtime/{id}`. All paths are relative to
 * the configured alerts API base URL (which carries the API version prefix).
 * Users supply only `live_stream_url`, `alert_type`, and `prompt`.
 *
 * The "Alert Verification" tab is rendered as a disabled placeholder; its
 * implementation is not yet wired up.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button } from '@nvidia/foundations-react-core';
import {
  IconTrash,
  IconPlus,
  IconCopy,
  IconArrowsUpDown,
  IconArrowUp,
  IconArrowDown,
  IconBolt,
  IconShieldCheck,
  IconDeviceFloppy,
  IconAlertCircle,
  IconLoader2,
  IconCheck,
  IconX,
} from '@tabler/icons-react';
import { AlertRulesType, RealtimeAlertRuleDraft, RealtimeAlertRule } from '../types';
import { useRealtimeAlertRules } from '../hooks/useRealtimeAlertRules';
import { VstStreamThumbnail } from './VstStreamThumbnail';

interface CreateAlertRulesViewProps {
  isDark: boolean;
  activeKind: AlertRulesType;
  onAddNew: () => void;
  /** vss-alert-bridge base URL (NEXT_PUBLIC_ALERTS_API_URL). */
  alertsApiUrl?: string;
  /** Base URL of the VST service (NEXT_PUBLIC_VST_API_URL); used for sensor thumbnails. */
  vstApiUrl?: string;
}

type RealtimeSortKey = 'alert_type' | 'prompt' | 'status' | null;
type SortDirection = 'asc' | 'desc' | null;

const KIND_TABS: Array<{
  id: AlertRulesType;
  label: string;
  icon: React.ReactNode;
  disabled?: boolean;
  disabledReason?: string;
}> = [
  { id: 'real-time', label: 'Real-time Alerts', icon: <IconBolt size={14} /> },
  {
    id: 'verification',
    label: 'Alert Verification',
    icon: <IconShieldCheck size={14} />,
    disabled: true,
    disabledReason: 'Coming soon',
  },
];

const generateDraftId = () =>
  `rt-draft-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;

/**
 * Last path segment of the RTSP URL, e.g.
 * `rtsp://.../sample-warehouse-ladder.mp4` → `sample-warehouse-ladder.mp4`.
 * Extension is kept — NVStreamer registers sensors with the full filename and
 * VST/alert-bridge lookups match by exact name.
 */
const deriveSensorName = (liveStreamUrl: string): string | undefined => {
  const trimmed = liveStreamUrl.trim();
  if (!trimmed) return undefined;
  const withoutQuery = trimmed.split(/[?#]/)[0];
  const segments = withoutQuery.split('/').filter(Boolean);
  const last = segments.at(-1);
  return last || undefined;
};

export const CreateAlertRulesView: React.FC<CreateAlertRulesViewProps> = ({
  isDark,
  activeKind,
  onAddNew,
  alertsApiUrl,
  vstApiUrl,
}) => {
  // `onAddNew` is the sidebar's "+ Create alert rule" handler; only the
  // realtime tab uses it today (it appends a new draft row via a module-level
  // bridge — see `triggerRealtimeAddDraft`). `void` it here so future-tab
  // wiring isn't blocked by an unused-prop lint.
  void onAddNew;
  // --- shared styles ---------------------------------------------------------
  const inputClass = `w-full rounded-md px-3 py-1.5 text-sm focus:outline-none transition-colors ${
    isDark
      ? 'bg-neutral-900 border border-neutral-700 text-neutral-100 placeholder-neutral-500 focus:border-[#76b900] focus:ring-1 focus:ring-[#76b900]/40'
      : 'bg-white border border-gray-300 text-gray-800 placeholder-gray-400 focus:border-green-500 focus:ring-1 focus:ring-green-200'
  }`;

  const readOnlyCellClass = `text-sm break-all ${
    isDark ? 'text-neutral-200' : 'text-gray-800'
  }`;

  const thClass = `text-left py-2 px-3 text-xs uppercase tracking-wider font-semibold ${
    isDark ? 'text-neutral-400' : 'text-gray-600'
  }`;

  // --- kind tabs -------------------------------------------------------------
  // Decorative only: there's only one enabled kind right now (`real-time`)
  // and the verification placeholder is permanently disabled. Tabs are not
  // interactive — selected state reflects `activeKind` for visual continuity.
  const kindTabs = (
    <div
      className={`flex-shrink-0 px-6 pt-4 border-b ${
        isDark ? 'bg-black border-neutral-700' : 'bg-white border-gray-200'
      }`}
    >
      <div role="tablist" aria-label="Alert kind" className="flex items-end gap-1">
        {KIND_TABS.map((tab) => {
          const isSelected = activeKind === tab.id && !tab.disabled;
          const baseClass = 'flex items-center gap-2 px-4 py-2 text-sm border-b-2 -mb-px';
          if (tab.disabled) {
            return (
              <button
                key={tab.id}
                role="tab"
                aria-selected={false}
                aria-disabled
                disabled
                title={tab.disabledReason ?? 'Disabled'}
                data-testid={`create-alert-kind-${tab.id}`}
                className={`${baseClass} border-transparent cursor-not-allowed opacity-50 ${
                  isDark ? 'text-neutral-500' : 'text-gray-400'
                }`}
              >
                {tab.icon}
                {tab.label}
              </button>
            );
          }
          return (
            <div
              key={tab.id}
              role="tab"
              aria-selected={isSelected}
              data-testid={`create-alert-kind-${tab.id}`}
              className={`${baseClass} ${
                isSelected
                  ? isDark
                    ? 'border-[#76b900] text-[#76b900] font-medium'
                    : 'border-green-600 text-green-700 font-medium'
                  : 'border-transparent ' +
                    (isDark ? 'text-neutral-400' : 'text-gray-500')
              }`}
            >
              {tab.icon}
              {tab.label}
            </div>
          );
        })}
      </div>
    </div>
  );

  const body = (
    <RealtimeAlertsTab
      isDark={isDark}
      alertsApiUrl={alertsApiUrl}
      vstApiUrl={vstApiUrl}
      inputClass={inputClass}
      readOnlyCellClass={readOnlyCellClass}
      thClass={thClass}
    />
  );

  return (
    <div
      data-testid="create-alert-rules-view"
      className={`flex flex-col h-full ${isDark ? 'bg-black text-neutral-100' : 'bg-gray-50 text-gray-900'}`}
    >
      {kindTabs}
      {body}
    </div>
  );
};

// =============================================================================
// Real-time Alerts tab
// =============================================================================

interface RealtimeAlertsTabProps {
  isDark: boolean;
  alertsApiUrl?: string;
  vstApiUrl?: string;
  inputClass: string;
  readOnlyCellClass: string;
  thClass: string;
}

const RealtimeAlertsTab: React.FC<RealtimeAlertsTabProps> = ({
  isDark,
  alertsApiUrl,
  vstApiUrl,
  inputClass,
  readOnlyCellClass,
  thClass,
}) => {
  const { rules, loading, error, createRule, deleteRule, refetch } = useRealtimeAlertRules({
    alertsApiUrl,
  });

  const [drafts, setDrafts] = useState<RealtimeAlertRuleDraft[]>([]);
  const [streamFilter, setStreamFilter] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  // Two-step delete confirmation: clicking trash on a saved rule sets this to
  // the rule id, swapping the action cell into "Confirm? [Delete] [Cancel]"
  // mode so users can't single-click their way into a destructive backend
  // call by accident.
  const [pendingDeleteId, setPendingDeleteId] = useState<string | null>(null);
  // Column sort for the saved-rules section. Three-state cycle on each header
  // click: asc → desc → cleared.
  const [realtimeSort, setRealtimeSort] = useState<{
    key: RealtimeSortKey;
    direction: SortDirection;
  }>({ key: null, direction: null });

  const handleRealtimeSort = useCallback((key: Exclude<RealtimeSortKey, null>) => {
    setRealtimeSort((prev) => {
      if (prev.key !== key || prev.direction === null) return { key, direction: 'asc' };
      if (prev.direction === 'asc') return { key, direction: 'desc' };
      return { key: null, direction: null };
    });
  }, []);

  const renderRealtimeSortIcon = (key: Exclude<RealtimeSortKey, null>) => {
    if (realtimeSort.key !== key || !realtimeSort.direction) {
      return <IconArrowsUpDown className="w-3.5 h-3.5 opacity-50" />;
    }
    return realtimeSort.direction === 'asc' ? (
      <IconArrowUp className="w-3.5 h-3.5" />
    ) : (
      <IconArrowDown className="w-3.5 h-3.5" />
    );
  };

  const addDraft = useCallback(() => {
    setDrafts((prev) => [
      ...prev,
      {
        draftId: generateDraftId(),
        live_stream_url: '',
        alert_type: '',
        prompt: '',
      },
    ]);
  }, []);

  const duplicateAsDraft = useCallback(
    (source: { live_stream_url: string; alert_type: string; prompt: string }) => {
      setDrafts((prev) => [
        ...prev,
        {
          draftId: generateDraftId(),
          live_stream_url: source.live_stream_url,
          alert_type: source.alert_type,
          prompt: source.prompt,
        },
      ]);
    },
    [],
  );

  // Expose `addDraft` to the rest of the app via a module-level ref so the
  // sidebar's "+ Add New Alert" button (handled in AlertsComponent) can add a
  // draft row without us hoisting all draft state up. Cleared on unmount.
  useEffect(() => {
    realtimeAddDraftRef.current = addDraft;
    return () => {
      if (realtimeAddDraftRef.current === addDraft) {
        realtimeAddDraftRef.current = null;
      }
    };
  }, [addDraft]);

  const updateDraft = useCallback((draftId: string, patch: Partial<RealtimeAlertRuleDraft>) => {
    setDrafts((prev) => prev.map((d) => (d.draftId === draftId ? { ...d, ...patch } : d)));
  }, []);

  const removeDraft = useCallback((draftId: string) => {
    setDrafts((prev) => prev.filter((d) => d.draftId !== draftId));
  }, []);

  const saveDraft = useCallback(
    async (draftId: string) => {
      const draft = drafts.find((d) => d.draftId === draftId);
      if (!draft) return;
      const live_stream_url = draft.live_stream_url.trim();
      const alert_type = draft.alert_type.trim();
      const prompt = draft.prompt.trim();
      if (!live_stream_url || !alert_type || !prompt) {
        updateDraft(draftId, {
          error: 'live_stream_url, alert_type, and prompt are required.',
        });
        return;
      }
      updateDraft(draftId, { saving: true, error: undefined });
      try {
        const sensor_name = deriveSensorName(live_stream_url);
        await createRule({
          live_stream_url,
          alert_type,
          prompt,
          ...(sensor_name ? { sensor_name } : {}),
        });
        // Drop the draft on success — the rule shows up in the rules list.
        setDrafts((prev) => prev.filter((d) => d.draftId !== draftId));
      } catch (err) {
        updateDraft(draftId, {
          saving: false,
          error: err instanceof Error ? err.message : 'Failed to create alert rule',
        });
      }
    },
    [drafts, createRule, updateDraft],
  );

  const handleDelete = useCallback(
    async (id: string) => {
      setDeletingId(id);
      setDeleteError(null);
      try {
        await deleteRule(id);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Failed to delete alert rule';
        console.error('Failed to delete realtime alert rule:', err);
        setDeleteError(message);
      } finally {
        setDeletingId((current) => (current === id ? null : current));
      }
    },
    [deleteRule],
  );

  const visibleRules: RealtimeAlertRule[] = useMemo(() => {
    const stream = streamFilter.trim().toLowerCase();
    const type = typeFilter.trim().toLowerCase();
    const filtered = rules.filter((rule) => {
      if (stream && !rule.live_stream_url.toLowerCase().includes(stream)) return false;
      if (type && !rule.alert_type.toLowerCase().includes(type)) return false;
      return true;
    });

    if (!realtimeSort.key || !realtimeSort.direction) return filtered;

    const dir = realtimeSort.direction === 'asc' ? 1 : -1;
    const key = realtimeSort.key;
    const valueOf = (rule: RealtimeAlertRule): string => {
      switch (key) {
        case 'alert_type':
          return rule.alert_type ?? '';
        case 'prompt':
          return rule.prompt ?? '';
        case 'status':
          return rule.status ?? '';
        default:
          return '';
      }
    };
    return [...filtered].sort(
      (a, b) => valueOf(a).localeCompare(valueOf(b), undefined, { sensitivity: 'base' }) * dir,
    );
  }, [rules, streamFilter, typeFilter, realtimeSort]);

  return (
    <>
      {/* Filter Row */}
      <div
        className={`flex-shrink-0 px-6 py-4 border-b ${
          isDark ? 'bg-black border-neutral-700' : 'bg-white border-gray-200'
        }`}
      >
        <div className="flex items-center gap-4 flex-wrap">
          <span className={`text-sm font-medium ${isDark ? 'text-neutral-300' : 'text-gray-700'}`}>
            Filter by
          </span>

          <div className="flex items-center gap-2">
            <label
              htmlFor="filter-stream-url"
              className={`text-sm whitespace-nowrap ${isDark ? 'text-neutral-400' : 'text-gray-600'}`}
            >
              Live Stream URL
            </label>
            <input
              id="filter-stream-url"
              data-testid="filter-stream-url"
              type="text"
              placeholder="Filter by URL"
              value={streamFilter}
              onChange={(e) => setStreamFilter(e.target.value)}
              className={`${inputClass} w-72`}
            />
          </div>

          <div className="flex items-center gap-2">
            <label
              htmlFor="filter-alert-type-rt"
              className={`text-sm whitespace-nowrap ${isDark ? 'text-neutral-400' : 'text-gray-600'}`}
            >
              Alert Type
            </label>
            <input
              id="filter-alert-type-rt"
              data-testid="filter-alert-type-rt"
              type="text"
              placeholder="Filter by type"
              value={typeFilter}
              onChange={(e) => setTypeFilter(e.target.value)}
              className={`${inputClass} w-48`}
            />
          </div>

          <div className="ml-auto flex items-center gap-3 text-xs">
            <button
              type="button"
              onClick={() => refetch()}
              className={`px-3 py-1 rounded border text-sm transition-colors ${
                isDark
                  ? 'border-neutral-700 text-neutral-200 hover:bg-neutral-800'
                  : 'border-gray-300 text-gray-700 hover:bg-gray-100'
              }`}
            >
              Refresh
            </button>
          </div>
        </div>
        {error && (
          <div
            data-testid="realtime-alerts-error"
            className={`mt-3 flex items-center gap-2 text-sm rounded-md px-3 py-2 ${
              isDark
                ? 'bg-red-500/10 text-red-300 border border-red-500/30'
                : 'bg-red-50 text-red-700 border border-red-200'
            }`}
          >
            <IconAlertCircle className="w-4 h-4 flex-shrink-0" />
            <span className="break-all">{error}</span>
          </div>
        )}
        {deleteError && (
          <div
            data-testid="realtime-alerts-delete-error"
            role="alert"
            className={`mt-3 flex items-center gap-2 text-sm rounded-md px-3 py-2 ${
              isDark
                ? 'bg-red-500/10 text-red-300 border border-red-500/30'
                : 'bg-red-50 text-red-700 border border-red-200'
            }`}
          >
            <IconAlertCircle className="w-4 h-4 flex-shrink-0" />
            <span className="flex-1 break-all">{deleteError}</span>
            <button
              type="button"
              onClick={() => setDeleteError(null)}
              aria-label="Dismiss delete error"
              className={`flex-shrink-0 rounded p-0.5 transition-colors ${
                isDark ? 'hover:bg-red-500/20' : 'hover:bg-red-100'
              }`}
            >
              <IconX className="w-3.5 h-3.5" />
            </button>
          </div>
        )}
      </div>

      {/* Rules + Drafts Table */}
      <div className="flex-1 overflow-auto pb-4">
        <table
          data-testid="realtime-alerts-table"
          className={`w-full border-collapse ${isDark ? '' : 'bg-white'}`}
        >
          <thead
            className={`sticky top-0 z-10 border-b ${
              isDark ? 'bg-neutral-900 border-neutral-700' : 'bg-gray-100 border-gray-300'
            }`}
          >
            <tr>
              <th className={`${thClass} w-12`} />
              <th className={`${thClass} w-36`}>Thumbnail</th>
              <th className={thClass}>Live Stream URL</th>
              <th className={`${thClass} w-48`}>
                <button
                  type="button"
                  onClick={() => handleRealtimeSort('alert_type')}
                  className="flex items-center gap-1.5 uppercase tracking-wider font-semibold"
                >
                  Alert Type {renderRealtimeSortIcon('alert_type')}
                </button>
              </th>
              <th className={thClass}>
                <button
                  type="button"
                  onClick={() => handleRealtimeSort('prompt')}
                  className="flex items-center gap-1.5 uppercase tracking-wider font-semibold"
                >
                  Prompt {renderRealtimeSortIcon('prompt')}
                </button>
              </th>
              <th className={`${thClass} w-44`}>
                <button
                  type="button"
                  onClick={() => handleRealtimeSort('status')}
                  className="flex items-center gap-1.5 uppercase tracking-wider font-semibold"
                >
                  Status {renderRealtimeSortIcon('status')}
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            {loading && rules.length === 0 && drafts.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className={`text-center py-10 text-sm ${
                    isDark ? 'text-neutral-400' : 'text-gray-500'
                  }`}
                >
                  <span className="inline-flex items-center gap-2">
                    <IconLoader2 className="w-4 h-4 animate-spin" />
                    Loading alert rules…
                  </span>
                </td>
              </tr>
            ) : visibleRules.length === 0 && drafts.length === 0 ? (
              <tr>
                <td
                  colSpan={6}
                  className={`text-center py-10 text-sm ${
                    isDark ? 'text-neutral-400' : 'text-gray-500'
                  }`}
                >
                  No real-time alert rules. Click &ldquo;+ Create alert rule&rdquo; to create one.
                </td>
              </tr>
            ) : (
              <>
                {visibleRules.map((rule, index) => (
                  <tr
                    key={rule.id}
                    data-testid="realtime-alert-row"
                    className={`border-b transition-colors ${
                      isDark
                        ? `border-neutral-800 hover:bg-neutral-900 ${
                            index % 2 === 0 ? 'bg-black' : 'bg-neutral-950'
                          }`
                        : `border-gray-200 hover:bg-gray-50 ${
                            index % 2 === 0 ? 'bg-white' : 'bg-gray-50'
                          }`
                    }`}
                  >
                    <td className="py-2 px-3 align-middle">
                      <div className="flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() =>
                            duplicateAsDraft({
                              live_stream_url: rule.live_stream_url,
                              alert_type: rule.alert_type,
                              prompt: rule.prompt,
                            })
                          }
                          aria-label={`Duplicate alert rule ${rule.id}`}
                          title="Duplicate rule"
                          data-testid="realtime-alert-copy"
                          className={`p-1.5 rounded transition-colors ${
                            isDark
                              ? 'text-neutral-400 hover:text-[#76b900] hover:bg-neutral-800'
                              : 'text-gray-500 hover:text-green-600 hover:bg-gray-100'
                          }`}
                        >
                          <IconCopy className="w-4 h-4" />
                        </button>
                        {pendingDeleteId === rule.id ? (
                          <>
                            <button
                              type="button"
                              onClick={() => {
                                setPendingDeleteId(null);
                                handleDelete(rule.id);
                              }}
                              disabled={deletingId === rule.id}
                              aria-label={`Confirm delete of alert rule ${rule.id}`}
                              title="Confirm delete"
                              data-testid="realtime-alert-confirm-delete"
                              className={`p-1.5 rounded transition-colors disabled:opacity-50 ${
                                isDark
                                  ? 'text-red-400 bg-red-500/10 hover:bg-red-500/20'
                                  : 'text-red-600 bg-red-50 hover:bg-red-100'
                              }`}
                            >
                              {deletingId === rule.id ? (
                                <IconLoader2 className="w-4 h-4 animate-spin" />
                              ) : (
                                <IconCheck className="w-4 h-4" />
                              )}
                            </button>
                            <button
                              type="button"
                              onClick={() => setPendingDeleteId(null)}
                              aria-label="Cancel delete"
                              title="Cancel"
                              data-testid="realtime-alert-cancel-delete"
                              className={`p-1.5 rounded transition-colors ${
                                isDark
                                  ? 'text-neutral-400 hover:text-neutral-200 hover:bg-neutral-800'
                                  : 'text-gray-500 hover:text-gray-800 hover:bg-gray-100'
                              }`}
                            >
                              <IconX className="w-4 h-4" />
                            </button>
                          </>
                        ) : (
                          <button
                            type="button"
                            onClick={() => setPendingDeleteId(rule.id)}
                            disabled={deletingId === rule.id}
                            aria-label={`Delete alert rule ${rule.id}`}
                            title="Delete"
                            className={`p-1.5 rounded transition-colors disabled:opacity-50 ${
                              isDark
                                ? 'text-neutral-400 hover:text-red-400 hover:bg-neutral-800'
                                : 'text-gray-500 hover:text-red-600 hover:bg-gray-100'
                            }`}
                          >
                            {deletingId === rule.id ? (
                              <IconLoader2 className="w-4 h-4 animate-spin" />
                            ) : (
                              <IconTrash className="w-4 h-4" />
                            )}
                          </button>
                        )}
                      </div>
                    </td>
                    <td className="py-2 px-3 align-middle">
                      <VstStreamThumbnail
                        isDark={isDark}
                        vstApiUrl={vstApiUrl}
                        // Prefer the rule's server-side `sensor_name`. Fall
                        // back to deriving from the RTSP URL for older rules
                        // that pre-date the sensor_name field.
                        sensorName={rule.sensor_name ?? deriveSensorName(rule.live_stream_url) ?? ''}
                      />
                    </td>
                    <td className={`py-2 px-3 align-top ${readOnlyCellClass}`}>
                      {rule.live_stream_url}
                    </td>
                    <td className={`py-2 px-3 align-top ${readOnlyCellClass}`}>{rule.alert_type}</td>
                    <td className={`py-2 px-3 align-top ${readOnlyCellClass}`}>{rule.prompt}</td>
                    <td className={`py-2 px-3 align-top text-xs ${readOnlyCellClass}`}>
                      <span
                        className={`inline-block px-2 py-0.5 rounded border ${
                          isDark
                            ? 'border-emerald-500/30 text-emerald-300 bg-emerald-500/10'
                            : 'border-emerald-200 text-emerald-700 bg-emerald-50'
                        }`}
                      >
                        {rule.status ?? 'active'}
                      </span>
                      {rule.created_at && (
                        <div className={`mt-1 ${isDark ? 'text-neutral-300' : 'text-gray-600'}`}>
                          {new Date(rule.created_at).toLocaleString()}
                        </div>
                      )}
                    </td>
                  </tr>
                ))}
                {drafts.map((draft, index) => {
                  const totalIndex = visibleRules.length + index;
                  return (
                    <tr
                      key={draft.draftId}
                      data-testid="realtime-alert-draft-row"
                      className={`border-b transition-colors ${
                        isDark
                          ? `border-neutral-800 hover:bg-neutral-900 ${
                              totalIndex % 2 === 0 ? 'bg-black' : 'bg-neutral-950'
                            }`
                          : `border-gray-200 hover:bg-gray-50 ${
                              totalIndex % 2 === 0 ? 'bg-white' : 'bg-gray-50'
                            }`
                      }`}
                    >
                      <td className="py-2 px-3 align-middle">
                        <div className="flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() =>
                              duplicateAsDraft({
                                live_stream_url: draft.live_stream_url,
                                alert_type: draft.alert_type,
                                prompt: draft.prompt,
                              })
                            }
                            aria-label="Duplicate draft rule"
                            title="Duplicate rule"
                            data-testid="realtime-alert-draft-copy"
                            className={`p-1.5 rounded transition-colors ${
                              isDark
                                ? 'text-neutral-400 hover:text-[#76b900] hover:bg-neutral-800'
                                : 'text-gray-500 hover:text-green-600 hover:bg-gray-100'
                            }`}
                          >
                            <IconCopy className="w-4 h-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => removeDraft(draft.draftId)}
                            aria-label="Discard draft"
                            title="Discard"
                            className={`p-1.5 rounded transition-colors ${
                              isDark
                                ? 'text-neutral-400 hover:text-red-400 hover:bg-neutral-800'
                                : 'text-gray-500 hover:text-red-600 hover:bg-gray-100'
                            }`}
                          >
                            <IconTrash className="w-4 h-4" />
                          </button>
                        </div>
                      </td>
                      <td className="py-2 px-3 align-middle">
                        <VstStreamThumbnail
                          isDark={isDark}
                          vstApiUrl={vstApiUrl}
                          sensorName={deriveSensorName(draft.live_stream_url) ?? ''}
                        />
                      </td>
                      <td className="py-2 px-3 align-top">
                        <input
                          type="text"
                          placeholder="rtsp://host:port/path"
                          value={draft.live_stream_url}
                          onChange={(e) =>
                            updateDraft(draft.draftId, { live_stream_url: e.target.value })
                          }
                          className={inputClass}
                        />
                      </td>
                      <td className="py-2 px-3 align-top">
                        <input
                          type="text"
                          placeholder="e.g. collision"
                          value={draft.alert_type}
                          onChange={(e) => updateDraft(draft.draftId, { alert_type: e.target.value })}
                          className={inputClass}
                        />
                      </td>
                      <td className="py-2 px-3 align-top">
                        <input
                          type="text"
                          placeholder="Detect any vehicle collisions"
                          value={draft.prompt}
                          onChange={(e) => updateDraft(draft.draftId, { prompt: e.target.value })}
                          className={inputClass}
                        />
                      </td>
                      <td className="py-2 px-3 align-top">
                        <div className="flex flex-col items-stretch gap-1">
                          {/*
                            Subtle primary action: thin green border + green
                            icon/text on a neutral gray fill that matches the
                            row's input cells. `min-w-[110px]` locks the
                            width across the Save / Saving… text swap so the
                            row layout doesn't jitter while saving.
                          */}
                          <button
                            type="button"
                            onClick={() => saveDraft(draft.draftId)}
                            disabled={!!draft.saving}
                            data-testid="realtime-alert-draft-save"
                            className={`min-w-[110px] flex items-center justify-center gap-1.5 px-3 py-1.5 rounded-md border border-[#76b900] text-[#76b900] text-sm font-medium transition-colors disabled:opacity-60 disabled:cursor-not-allowed ${
                              isDark
                                ? 'bg-neutral-950 hover:bg-neutral-900'
                                : 'bg-gray-200 hover:bg-gray-300'
                            }`}
                          >
                            {draft.saving ? (
                              <IconLoader2
                                size={16}
                                color="#76b900"
                                className="animate-spin"
                              />
                            ) : (
                              <IconDeviceFloppy size={16} color="#76b900" />
                            )}
                            {draft.saving ? 'Saving…' : 'Save'}
                          </button>
                          {draft.error && (
                            <span
                              className={`text-xs flex items-start gap-1 ${
                                isDark ? 'text-red-300' : 'text-red-600'
                              }`}
                            >
                              <IconAlertCircle className="w-3.5 h-3.5 mt-0.5 flex-shrink-0" />
                              <span className="break-all">{draft.error}</span>
                            </span>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </>
            )}
          </tbody>
        </table>

        <div className="flex justify-center pt-6">
          <Button kind="secondary" onClick={addDraft} data-testid="add-new-alert-button-inline">
            <span className="flex items-center gap-2">
              <IconPlus
                size={16}
                color={isDark ? '#ffffff' : '#374151'}
                style={{ color: isDark ? '#ffffff' : '#374151' }}
              />
              Create alert rule
            </span>
          </Button>
        </div>
      </div>
    </>
  );
};

// ---------------------------------------------------------------------------
// Bridge: lets the sidebar's "+ Create alert rule" button append a draft row
// in the realtime tab without lifting all draft state up to AlertsComponent.
// AlertsComponent calls `triggerRealtimeAddDraft()` after switching into the
// create view; that hits the ref set by RealtimeAlertsTab's mount effect and
// invokes its `addDraft` callback.
// ---------------------------------------------------------------------------
const realtimeAddDraftRef: { current: (() => void) | null } = { current: null };

export const triggerRealtimeAddDraft = (): boolean => {
  const fn = realtimeAddDraftRef.current;
  if (fn) {
    fn();
    return true;
  }
  return false;
};

