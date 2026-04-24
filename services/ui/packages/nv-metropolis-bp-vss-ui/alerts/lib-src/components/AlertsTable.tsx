// SPDX-License-Identifier: MIT
/**
 * AlertsTable Component - Advanced Data Table for Alerts Management
 *
 * This file contains the AlertsTable component which provides a sophisticated data table
 * interface for displaying, sorting, and managing security alerts and incidents. The component
 * features advanced functionality including sortable columns, expandable rows for detailed
 * metadata viewing, real-time filtering capabilities, and integrated video playback controls.
 *
 * **Key Features:**
 * - Sortable timestamp column with three-state sorting (ascending, descending, default)
 * - Expandable rows revealing comprehensive alert metadata and analytics information
 * - Real-time filtering integration with dynamic filter tag application
 * - Video playback integration for alert-related footage and evidence
 * - Responsive design with comprehensive light/dark theme support
 * - Loading states, error handling, and empty state management
 * - Accessibility features including keyboard navigation and screen reader support
 * - Performance optimizations with React.memo and useMemo for large datasets
 *
 * **Data Flow:**
 * - Receives alerts data from parent component via props
 * - Applies client-side sorting based on timestamp values
 * - Manages expandable row state for detailed metadata viewing
 * - Communicates filter selections back to parent via callback props
 * - Handles video playback requests through integrated modal system
 */

import React, { useState, useCallback, useMemo, useEffect, useRef, useLayoutEffect } from 'react';
import { Button } from '@nvidia/foundations-react-core';
import {
  IconChevronDown,
  IconChevronUp,
  IconRefresh,
  IconInfoCircle,
  IconArrowsUpDown,
  IconArrowUp,
  IconArrowDown,
} from '@tabler/icons-react';
import { AlertData, FilterState, FilterType, VLM_VERDICT } from '../types';
import { formatAlertTimestamp } from '../utils/timeUtils';
import { MetadataSection } from './MetadataSection';
import { ThumbnailButton } from './ThumbnailButton';
import { TimeFormatSwitch, type TimeFormat } from './TimeFormatSwitch';
export type PaginationPageItem = number | 'ellipsis';

/**
 * Build page numbers for Previous … 1 2 3 4 5 … Next style controls.
 */
export function getPaginationPageItems(current: number, total: number): PaginationPageItem[] {
  if (total <= 1) return [1];
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }

  if (current <= 3) {
    const base: PaginationPageItem[] = [1, 2, 3, 4, 5];
    return [...base, 'ellipsis', total];
  }

  if (current >= total - 2) {
    const start = total - 4;
    const tail = Array.from({ length: total - start + 1 }, (_, i) => start + i);
    return [1, 'ellipsis', ...tail];
  }

  return [1, 'ellipsis', current - 1, current, current + 1, 'ellipsis', total];
}

function resolvePageSizeActive(paginate: boolean, pageSize?: number): number {
  if (!paginate) {
    return 1;
  }
  if (pageSize != null && pageSize > 0) {
    return pageSize;
  }
  return 100;
}

function sortAlertsByColumn(
  alerts: AlertData[],
  sortKey: 'timestamp' | 'end',
  direction: 'asc' | 'desc',
): AlertData[] {
  return [...alerts].sort((a, b) => {
    const aValue = a[sortKey] || '';
    const bValue = b[sortKey] || '';

    if (!aValue && !bValue) return 0;
    if (!aValue) return 1;
    if (!bValue) return -1;

    const aTime = new Date(aValue).getTime();
    const bTime = new Date(bValue).getTime();

    if (Number.isNaN(aTime) && Number.isNaN(bTime)) return 0;
    if (Number.isNaN(aTime)) return 1;
    if (Number.isNaN(bTime)) return -1;

    const result = aTime - bTime;
    if (direction === 'desc') {
      return -result;
    }
    return result;
  });
}

function dataRowShellClasses(isDark: boolean, stripeIndex: number): string {
  const even = stripeIndex % 2 === 0;
  let stripe: string;
  if (isDark) {
    stripe = even ? 'bg-black' : 'bg-neutral-950';
  } else {
    stripe = even ? 'bg-white' : 'bg-gray-50';
  }
  const borderHover = isDark ? 'border-neutral-700 hover:bg-neutral-800' : 'border-gray-200 hover:bg-gray-100';
  return `border-b transition-colors ${borderHover} ${stripe}`;
}

const PAGE_SIZE_PRESETS = [10, 20, 50, 100, 200, 500];
const PAGE_SIZE_CUSTOM_VALUE = '-1';

interface AlertsTableProps {
  alerts: AlertData[];
  loading: boolean;
  error: string | null;
  isDark: boolean;
  activeFilters: FilterState;
  onAddFilter: (type: FilterType, value: string) => void;
  onPlayVideo: (alert: AlertData) => void;
  loadingAlertId?: string | null;
  onRefresh: () => void;
  alertReportPromptTemplate?: string;
  vstApiUrl?: string;
  sensorMap?: Map<string, string>;
  showObjectsBbox?: boolean;
  timeFormat?: TimeFormat;
  /** Called when the user toggles the UTC / Local switch in the table toolbar. */
  onTimeFormatChange?: (format: TimeFormat) => void;
  /**
   * Rows per page (client-side). Omit or `undefined` → **100** so total pages grow when data/load-more grows.
   * Pass **0** to disable pagination (show all rows on one page).
   */
  pageSize?: number;
  /** Called when the user picks a new rows-per-page value from the table toolbar. */
  onPageSizeChange?: (size: number) => void;
  /** Change when filters/time/VLM scope changes so pagination resets to page 1. */
  paginationResetKey?: string;
  /** Shown on last page when API may have more rows (same as NEXT_PUBLIC_ALERTS_TAB_ALERTS_FETCH_MAX_RESULT_SIZE). */
  loadMoreBatchSize?: number;
  canLoadMore?: boolean;
  loadingMore?: boolean;
  /** Hook uses fromTimestamp = now − period and toTimestamp = min(`end`) over all loaded alerts minus a small fixed offset (not tied to current page). */
  onLoadMore?: () => void | Promise<unknown>;
  /** Increments after a successful load-more; resets column sort without changing current page. */
  loadMoreCompletionCount?: number;
  /** When true, "Show more" is disabled because auto-refresh would overwrite appended rows. */
  autoRefreshEnabled?: boolean;
}

type SortConfig = {
  key: 'timestamp' | 'end' | null;
  direction: 'asc' | 'desc' | null;
};

type SortColumnIconsProps = Readonly<{
  columnKey: 'timestamp' | 'end';
  sortKey: 'timestamp' | 'end' | null;
  direction: 'asc' | 'desc' | null;
}>;

function SortColumnIcons({ columnKey, sortKey, direction }: SortColumnIconsProps) {
  if (sortKey === columnKey && direction === 'asc') {
    return <IconArrowUp className="w-4 h-4" />;
  }
  if (sortKey === columnKey && direction === 'desc') {
    return <IconArrowDown className="w-4 h-4" />;
  }
  return <IconArrowsUpDown className="w-4 h-4 opacity-50" />;
}

function getAriaSort(
  columnKey: NonNullable<SortConfig['key']>,
  sortConfig: SortConfig,
): 'ascending' | 'descending' | 'none' {
  if (sortConfig.key !== columnKey) return 'none';
  if (sortConfig.direction === 'asc') return 'ascending';
  if (sortConfig.direction === 'desc') return 'descending';
  return 'none';
}

const VERDICT_STYLES_DARK: Record<string, string> = {
  [VLM_VERDICT.CONFIRMED]: 'text-green-400 bg-green-500/10 border-green-500/30',
  [VLM_VERDICT.REJECTED]: 'text-red-400 bg-red-500/10 border-red-500/30',
  [VLM_VERDICT.VERIFICATION_FAILED]: 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30',
  [VLM_VERDICT.NOT_CONFIRMED]: 'text-gray-400 bg-gray-500/10 border-gray-500/30',
};
const VERDICT_STYLES_LIGHT: Record<string, string> = {
  [VLM_VERDICT.CONFIRMED]: 'text-green-700 bg-green-50 border-green-200',
  [VLM_VERDICT.REJECTED]: 'text-red-700 bg-red-50 border-red-200',
  [VLM_VERDICT.VERIFICATION_FAILED]: 'text-yellow-700 bg-yellow-50 border-yellow-200',
  [VLM_VERDICT.NOT_CONFIRMED]: 'text-gray-700 bg-gray-50 border-gray-200',
};
const VERDICT_FALLBACK_DARK = 'text-gray-400 bg-gray-500/10 border-gray-500/30';
const VERDICT_FALLBACK_LIGHT = 'text-gray-700 bg-gray-50 border-gray-200';

type VlmVerdictCellProps = Readonly<{ alert: AlertData; isDark: boolean }>;

function VlmVerdictCell({ alert, isDark }: VlmVerdictCellProps) {
  const verdict = alert.metadata?.analyticsModule?.info?.verdict || alert.metadata?.info?.verdict;
  if (!verdict) {
    return <>N/A</>;
  }

  const styles = isDark ? VERDICT_STYLES_DARK : VERDICT_STYLES_LIGHT;
  const fallback = isDark ? VERDICT_FALLBACK_DARK : VERDICT_FALLBACK_LIGHT;
  const style = styles[verdict] ?? fallback;

  const displayText = verdict
    .split('-')
    .map((word: string) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');

  return (
    <span data-testid="alert-vlm-verdict" className={`inline-block px-2 py-1 rounded text-xs font-medium border ${style}`}>{displayText}</span>
  );
}

type AlertsTableLoadingViewProps = Readonly<{ isDark: boolean }>;

function AlertsTableLoadingView({ isDark }: AlertsTableLoadingViewProps) {
  return (
    <div className="flex items-center justify-center h-full">
      <div className="text-center">
        <IconRefresh className={`w-8 h-8 animate-spin mx-auto mb-3 ${isDark ? 'text-green-400' : 'text-green-500'}`} />
        <p className={`text-base font-medium ${isDark ? 'text-neutral-300' : 'text-gray-700'}`}>Loading alerts...</p>
      </div>
    </div>
  );
}

type AlertsTableErrorViewProps = Readonly<{
  isDark: boolean;
  error: string;
  onRefresh: () => void;
}>;

function AlertsTableErrorView({
  isDark,
  error,
  onRefresh,
}: AlertsTableErrorViewProps) {
  return (
    <div className="flex items-center justify-center h-full">
      <div className={`text-center p-6 rounded-lg ${isDark ? 'bg-red-500/10 border border-red-500/20' : 'bg-red-50'}`}>
        <p className={`font-bold text-lg mb-2 ${isDark ? 'text-red-400' : 'text-red-700'}`}>Error loading alerts</p>
        <div
          className={`text-sm mb-4 max-h-24 overflow-auto rounded p-3 break-words whitespace-pre-wrap ${
            isDark ? 'bg-black/50 text-gray-300' : 'bg-red-100/50 text-red-600 border border-red-200'
          }`}
        >
          <p className={isDark ? 'text-gray-300' : 'text-red-600'}>{error}</p>
        </div>
        <Button
          kind="primary"
          onClick={onRefresh}
        >
          Retry
        </Button>
      </div>
    </div>
  );
}

type AlertsTableEmptyViewProps = Readonly<{ isDark: boolean }>;

function AlertsTableEmptyView({ isDark }: AlertsTableEmptyViewProps) {
  return (
    <div className="flex items-center justify-center h-full">
      <p className={`text-base font-medium ${isDark ? 'text-gray-300' : 'text-gray-600'}`}>
        No results found (Verify that the database has alert data).
      </p>
    </div>
  );
}

type AlertsTablePaginationNavProps = Readonly<{
  isDark: boolean;
  currentPage: number;
  pageCount: number;
  paginationPageItems: PaginationPageItem[];
  setCurrentPage: React.Dispatch<React.SetStateAction<number>>;
}>;

function AlertsTablePaginationNav({
  isDark,
  currentPage,
  pageCount,
  paginationPageItems,
  setCurrentPage,
}: AlertsTablePaginationNavProps) {
  return (
    <nav className="flex flex-wrap items-center gap-1 shrink-0" aria-label="Table pagination">
      <Button
        kind="tertiary"
        size="small"
        disabled={currentPage <= 1}
        onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
      >
        Previous
      </Button>
      {paginationPageItems.map((item, idx) => {
        if (item === 'ellipsis') {
          const prev = paginationPageItems[idx - 1];
          const next = paginationPageItems[idx + 1];
          const ellipsisKey = `ellipsis-${String(prev)}-${String(next)}`;
          return (
            <span
              key={ellipsisKey}
              className={`inline-flex min-w-[2rem] items-center justify-center px-1 text-sm select-none ${
                isDark ? 'text-gray-500' : 'text-gray-400'
              }`}
              aria-hidden
            >
              …
            </span>
          );
        }
        return (
          <Button
            key={`page-${item}`}
            kind={item === currentPage ? 'primary' : 'tertiary'}
            size="small"
            onClick={() => setCurrentPage(item)}
            aria-label={`Page ${item}`}
            aria-current={item === currentPage ? 'page' : undefined}
          >
            {item}
          </Button>
        );
      })}
      <Button
        kind="tertiary"
        size="small"
        disabled={currentPage >= pageCount}
        onClick={() => setCurrentPage((p) => Math.min(pageCount, p + 1))}
      >
        Next
      </Button>
    </nav>
  );
}

type AlertTableBodyRowProps = Readonly<{
  alert: AlertData;
  stripeIndex: number;
  isDark: boolean;
  isExpanded: boolean;
  timeFormatUtc: boolean;
  activeFilters: FilterState;
  loadingAlertId?: string | null;
  vstApiUrl?: string;
  sensorMap?: Map<string, string>;
  showObjectsBbox: boolean;
  alertReportPromptTemplate?: string;
  tdTextClass: string;
  toggleRow: (id: string) => void;
  onAddFilter: (type: FilterType, value: string) => void;
  onPlayVideo: (alert: AlertData) => void;
}>;

const AlertTableBodyRow = React.memo(function AlertTableBodyRow({
  alert,
  stripeIndex,
  isDark,
  isExpanded,
  timeFormatUtc,
  activeFilters,
  loadingAlertId,
  vstApiUrl,
  sensorMap,
  showObjectsBbox,
  alertReportPromptTemplate,
  tdTextClass,
  toggleRow,
  onAddFilter,
  onPlayVideo,
}: AlertTableBodyRowProps) {
  return (
    <>
      <tr data-testid="alert-row" className={dataRowShellClasses(isDark, stripeIndex)}>
        <td className="py-3 px-4 text-sm">
          <button
            type="button"
            onClick={() => toggleRow(alert.id)}
            aria-label={`${isExpanded ? 'Collapse' : 'Expand'} alert ${alert.id}`}
            aria-expanded={isExpanded}
            className={`p-1.5 rounded transition-colors text-gray-400 ${
              isDark ? 'hover:text-white hover:bg-neutral-700' : 'hover:text-gray-900 hover:bg-neutral-100'
            }`}
          >
            {isExpanded ? <IconChevronUp className="w-4 h-4" /> : <IconChevronDown className="w-4 h-4" />}
          </button>
        </td>
        <td className="py-3 px-4 text-sm">
          <ThumbnailButton
            alert={alert}
            vstApiUrl={vstApiUrl}
            sensorMap={sensorMap}
            isDark={isDark}
            onPlayVideo={onPlayVideo}
            isLoading={loadingAlertId === alert.id}
            showObjectsBbox={showObjectsBbox}
          />
        </td>
        <td className={tdTextClass}>
          {alert.timestamp ? formatAlertTimestamp(alert.timestamp, timeFormatUtc) : 'N/A'}
        </td>
        <td className={tdTextClass}>{alert.end ? formatAlertTimestamp(alert.end, timeFormatUtc) : 'N/A'}</td>
        <td className="py-3 px-4 text-sm">
          <Button
            kind="tertiary"
            onClick={() => {
              if (!activeFilters.sensors.has(alert.sensor)) {
                onAddFilter('sensors', alert.sensor);
              }
            }}
          >
            {alert.sensor ? alert.sensor : 'N/A'}
          </Button>
        </td>
        <td className="py-3 px-4 text-sm">
          <Button
            kind="tertiary"
            onClick={() => {
              if (!activeFilters.alertTypes.has(alert.alertType)) {
                onAddFilter('alertTypes', alert.alertType);
              }
            }}
          >
            {alert.alertType ? alert.alertType : 'N/A'}
          </Button>
        </td>
        <td className="py-3 px-4 text-sm">
          {alert.alertTriggered ? (
            <Button
              kind="tertiary"
              onClick={() => {
                if (!activeFilters.alertTriggered.has(alert.alertTriggered)) {
                  onAddFilter('alertTriggered', alert.alertTriggered);
                }
              }}
            >
              {alert.alertTriggered}
            </Button>
          ) : (
            <span className={isDark ? 'text-neutral-300' : 'text-gray-600'}>N/A</span>
          )}
        </td>
        <td className={tdTextClass}>
          <VlmVerdictCell alert={alert} isDark={isDark} />
        </td>
        <td className={tdTextClass}>{alert.alertDescription ? alert.alertDescription : 'N/A'}</td>
        <td className="py-3 px-4 text-sm">
          <button
            type="button"
            onClick={() => toggleRow(alert.id)}
            aria-label={`Show details for alert ${alert.id}`}
            aria-expanded={isExpanded}
            className={`p-1.5 rounded transition-colors text-gray-400 ${
              isDark ? 'hover:text-white hover:bg-neutral-700' : 'hover:text-gray-900 hover:bg-neutral-100'
            }`}
          >
            <IconInfoCircle className="w-4 h-4" />
          </button>
        </td>
      </tr>
      {isExpanded && (
        <tr data-testid="alert-row-expanded" className={isDark ? 'bg-black border-b border-neutral-700' : 'bg-gray-100 border-b border-gray-200'}>
          <td></td>
          <td></td>
          <td colSpan={8} className="py-4 pr-4">
            <div className="space-y-4">
              <MetadataSection
                alertId={alert.id}
                sensor={alert.sensor}
                title="Metadata"
                data={alert.metadata}
                isDark={isDark}
                alertReportPromptTemplate={alertReportPromptTemplate}
              />
            </div>
          </td>
        </tr>
      )}
    </>
  );
});

type AlertsTableContentProps = Readonly<{
  toolbarRef: React.RefObject<HTMLDivElement>;
  isDark: boolean;
  stickyToolbarHeightPx: number;
  sortedAlerts: AlertData[];
  paginate: boolean;
  pageCount: number;
  currentPage: number;
  pageSizeActive: number;
  paginationPageItems: PaginationPageItem[];
  setCurrentPage: React.Dispatch<React.SetStateAction<number>>;
  onPageSizeChange?: (size: number) => void;
  timeFormat: TimeFormat;
  onTimeFormatChange?: (format: TimeFormat) => void;
  theadClass: string;
  thClass: string;
  tdTextClass: string;
  sortableThExtras: string;
  sortConfig: SortConfig;
  handleSort: (key: 'timestamp' | 'end') => void;
  displayedAlerts: AlertData[];
  expandedRows: Set<string>;
  timeFormatUtc: boolean;
  activeFilters: FilterState;
  loadingAlertId?: string | null;
  vstApiUrl?: string;
  sensorMap?: Map<string, string>;
  showObjectsBbox: boolean;
  alertReportPromptTemplate?: string;
  toggleRow: (id: string) => void;
  onAddFilter: (type: FilterType, value: string) => void;
  onPlayVideo: (alert: AlertData) => void;
  showLoadMore: boolean;
  loadMoreFooterClass: string;
  loadingMore: boolean;
  handleLoadMoreClick: () => void;
  loadMoreBatchSize?: number;
  autoRefreshEnabled: boolean;
}>;

function AlertsTableContent({
  toolbarRef,
  isDark,
  stickyToolbarHeightPx,
  sortedAlerts,
  paginate,
  pageCount,
  currentPage,
  pageSizeActive,
  paginationPageItems,
  setCurrentPage,
  onPageSizeChange,
  timeFormat: timeFormatProp,
  onTimeFormatChange,
  theadClass,
  thClass,
  tdTextClass,
  sortableThExtras,
  sortConfig,
  handleSort,
  displayedAlerts,
  expandedRows,
  timeFormatUtc,
  activeFilters,
  loadingAlertId,
  vstApiUrl,
  sensorMap,
  showObjectsBbox,
  alertReportPromptTemplate,
  toggleRow,
  onAddFilter,
  onPlayVideo,
  showLoadMore,
  loadMoreFooterClass,
  loadingMore,
  handleLoadMoreClick,
  loadMoreBatchSize,
  autoRefreshEnabled,
}: AlertsTableContentProps) {
  const [showCustomPageInput, setShowCustomPageInput] = useState(false);
  const [customPageValue, setCustomPageValue] = useState('');
  const [customPageError, setCustomPageError] = useState('');
  const customPageInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (showCustomPageInput && customPageInputRef.current) customPageInputRef.current.focus();
  }, [showCustomPageInput]);

  const applyCustomPageSize = useCallback(() => {
    const num = Number(customPageValue);
    if (!Number.isInteger(num) || num < 1 || num > 500) {
      setCustomPageError('1 – 500');
      return;
    }
    onPageSizeChange?.(num);
    setShowCustomPageInput(false);
    setCustomPageError('');
  }, [customPageValue, onPageSizeChange]);

  return (
    <div className="w-full">
      <div
        ref={toolbarRef as React.RefObject<HTMLDivElement>}
        className={`sticky top-0 z-30 px-4 py-2 border-b flex flex-wrap items-center justify-between gap-3 shadow-sm ${
          isDark ? 'bg-black border-neutral-700' : 'bg-white border-gray-300'
        }`}
      >
        <div className="flex flex-wrap items-center gap-3">
          <fieldset
            aria-labelledby="alerts-displayed-label"
            className={`inline-flex items-center gap-2 px-3 py-1 rounded-lg transition-all border-0 min-w-0 m-0 ${
              isDark ? 'bg-black/30 hover:bg-black/40' : 'bg-gray-100/60 hover:bg-gray-100'
            }`}
          >
            <span
              id="alerts-displayed-label"
              className={`text-xs font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`}
            >
              Alerts displayed:
            </span>
            <span
              data-testid="alerts-displayed-count"
              className={`inline-flex items-center justify-center px-2.5 py-0.5 rounded-full text-xs font-semibold border ${
                isDark ? 'bg-black text-white border-white' : 'bg-white text-gray-800 border-gray-400'
              }`}
              aria-live="polite"
            >
              {sortedAlerts.length}
            </span>
          </fieldset>
          {paginate && sortedAlerts.length > 0 && (
            <span className={`text-xs whitespace-nowrap ${isDark ? 'text-gray-400' : 'text-gray-600'}`}>
              Page {currentPage} of {pageCount}
              {' · '}
              Showing {(currentPage - 1) * pageSizeActive + 1}–
              {Math.min(currentPage * pageSizeActive, sortedAlerts.length)}
            </span>
          )}
          {paginate && onPageSizeChange && (
            <div className="flex items-center gap-1.5">
              <label
                htmlFor="table-page-size-select"
                className={`text-xs font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`}
              >
                Rows per page
              </label>
              {showCustomPageInput ? (
                <div className="flex items-center gap-1">
                  <input
                    ref={customPageInputRef}
                    type="number"
                    min={1}
                    max={500}
                    placeholder="1–500"
                    value={customPageValue}
                    onChange={(e) => { setCustomPageValue(e.target.value); setCustomPageError(''); }}
                    onKeyDown={(e) => { if (e.key === 'Enter') applyCustomPageSize(); }}
                    className={`w-16 rounded pl-2 pr-1 py-1 text-xs focus:outline-none transition-all ${
                      isDark
                        ? 'bg-black border border-gray-600 text-white focus:border-[#76b900] focus:ring-1 focus:ring-[#76b900]/40'
                        : 'bg-white border border-gray-300 text-gray-600 focus:ring-green-400'
                    } ${customPageError ? (isDark ? 'border-red-500' : 'border-red-400') : ''}`}
                  />
                  <button
                    type="button"
                    onClick={applyCustomPageSize}
                    className={`px-1.5 py-1 rounded text-xs font-medium transition-colors ${
                      isDark
                        ? 'bg-[#76b900] text-black hover:bg-[#8ad100]'
                        : 'bg-green-600 text-white hover:bg-green-700'
                    }`}
                  >
                    OK
                  </button>
                  <button
                    type="button"
                    onClick={() => { setShowCustomPageInput(false); setCustomPageError(''); }}
                    className={`px-1 py-1 rounded text-xs transition-colors ${
                      isDark ? 'text-gray-400 hover:text-white' : 'text-gray-500 hover:text-gray-700'
                    }`}
                  >
                    ✕
                  </button>
                </div>
              ) : (
                <select
                  id="table-page-size-select"
                  value={PAGE_SIZE_PRESETS.includes(pageSizeActive) ? String(pageSizeActive) : PAGE_SIZE_CUSTOM_VALUE}
                  className={`rounded pl-2 pr-6 py-1 text-xs focus:outline-none transition-all cursor-pointer ${
                    isDark
                      ? 'bg-black border border-gray-600 text-white hover:border-gray-500 focus:border-[#76b900] focus:ring-1 focus:ring-[#76b900]/40'
                      : 'bg-white border border-gray-300 text-gray-600 focus:ring-green-400 hover:border-gray-400'
                  }`}
                  onChange={(e) => {
                    const v = e.target.value;
                    if (v === PAGE_SIZE_CUSTOM_VALUE) {
                      setShowCustomPageInput(true);
                      setCustomPageValue(String(pageSizeActive));
                      setCustomPageError('');
                    } else {
                      onPageSizeChange(Number(v));
                    }
                  }}
                >
                  {PAGE_SIZE_PRESETS.map((p) => (
                    <option key={p} value={String(p)}>{p}</option>
                  ))}
                  {PAGE_SIZE_PRESETS.includes(pageSizeActive) ? (
                    <option value={PAGE_SIZE_CUSTOM_VALUE}>Custom</option>
                  ) : (
                    <option value={PAGE_SIZE_CUSTOM_VALUE}>{pageSizeActive} (custom)</option>
                  )}
                </select>
              )}
            </div>
          )}
          {onTimeFormatChange && (
            <div className="flex items-center gap-1.5">
              <span className={`text-xs font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
                Time display
              </span>
              <TimeFormatSwitch
                value={timeFormatProp}
                onChange={onTimeFormatChange}
                isDark={isDark}
              />
            </div>
          )}
        </div>
        {paginate && pageCount > 1 && (
          <AlertsTablePaginationNav
            isDark={isDark}
            currentPage={currentPage}
            pageCount={pageCount}
            paginationPageItems={paginationPageItems}
            setCurrentPage={setCurrentPage}
          />
        )}
      </div>
      <table data-testid="alerts-table" className="w-full border-collapse">
        <thead className={`sticky z-20 border-b ${theadClass}`} style={{ top: stickyToolbarHeightPx }}>
          <tr>
            <th className={`${thClass} w-8`}></th>
            <th className={`${thClass} w-8`}></th>
            <th
              aria-sort={getAriaSort('timestamp', sortConfig)}
              className={`${thClass} cursor-pointer select-none hover:bg-opacity-10 ${sortableThExtras}`}
              onClick={() => handleSort('timestamp')}
            >
              <div className="flex items-center gap-2">
                <span>Timestamp</span>
                <SortColumnIcons columnKey="timestamp" sortKey={sortConfig.key} direction={sortConfig.direction} />
              </div>
            </th>
            <th
              aria-sort={getAriaSort('end', sortConfig)}
              className={`${thClass} cursor-pointer select-none hover:bg-opacity-10 ${sortableThExtras}`}
              onClick={() => handleSort('end')}
            >
              <div className="flex items-center gap-2">
                <span>End</span>
                <SortColumnIcons columnKey="end" sortKey={sortConfig.key} direction={sortConfig.direction} />
              </div>
            </th>
            <th className={thClass}>Sensor</th>
            <th className={thClass}>Alert Type</th>
            <th className={thClass}>Alert Triggered</th>
            <th className={thClass}>VLM Verdict</th>
            <th className={thClass}>Alert Description</th>
            <th className={`${thClass} w-8`}></th>
          </tr>
        </thead>
        <tbody>
          {displayedAlerts.map((alert, index) => {
            const stripeIndex = paginate ? (currentPage - 1) * pageSizeActive + index : index;
            return (
              <AlertTableBodyRow
                key={alert.id}
                alert={alert}
                stripeIndex={stripeIndex}
                isDark={isDark}
                isExpanded={expandedRows.has(alert.id)}
                timeFormatUtc={timeFormatUtc}
                activeFilters={activeFilters}
                loadingAlertId={loadingAlertId}
                vstApiUrl={vstApiUrl}
                sensorMap={sensorMap}
                showObjectsBbox={showObjectsBbox}
                alertReportPromptTemplate={alertReportPromptTemplate}
                tdTextClass={tdTextClass}
                toggleRow={toggleRow}
                onAddFilter={onAddFilter}
                onPlayVideo={onPlayVideo}
              />
            );
          })}
        </tbody>
      </table>
      {showLoadMore && (
        <div className={`flex justify-center border-t px-4 py-3 ${loadMoreFooterClass}`}>
          <Button
            kind="tertiary"
            disabled={loadingMore || autoRefreshEnabled}
            onClick={handleLoadMoreClick}
            data-testid="alerts-load-more"
            title={autoRefreshEnabled ? 'Disable auto-refresh to load older alerts' : 'Load more alerts from server'}
          >
            {loadingMore ? 'Loading…' : `Load more alerts (up to ${loadMoreBatchSize ?? 0})`}
          </Button>
        </div>
      )}
    </div>
  );
}

export function AlertsTable({
  alerts,
  loading,
  error,
  isDark,
  activeFilters,
  onAddFilter,
  onPlayVideo,
  loadingAlertId,
  onRefresh,
  alertReportPromptTemplate,
  vstApiUrl,
  sensorMap,
  showObjectsBbox = false,
  timeFormat = 'local',
  onTimeFormatChange,
  pageSize,
  onPageSizeChange,
  paginationResetKey = '',
  loadMoreBatchSize,
  canLoadMore = false,
  loadingMore = false,
  onLoadMore,
  loadMoreCompletionCount = 0,
  autoRefreshEnabled = false,
}: Readonly<AlertsTableProps>) {
  const toolbarRef = useRef<HTMLDivElement>(null);
  /** Pixels; used as thead sticky offset so column headers sit below the sticky controls bar. */
  const [stickyToolbarHeightPx, setStickyToolbarHeightPx] = useState(56);

  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [sortConfig, setSortConfig] = useState<SortConfig>({ key: null, direction: null });

  const toggleRow = useCallback((id: string) => {
    setExpandedRows((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  }, []);

  const handleSort = useCallback((key: 'timestamp' | 'end') => {
    setExpandedRows(new Set());

    setSortConfig((prev) => {
      if (prev.key !== key || prev.direction === null) {
        return { key, direction: 'asc' };
      }
      if (prev.direction === 'asc') {
        return { key, direction: 'desc' };
      }
      return { key: null, direction: null };
    });
  }, []);

  const sortedAlerts = useMemo(() => {
    if (!sortConfig.key || !sortConfig.direction) {
      return alerts;
    }
    return sortAlertsByColumn(alerts, sortConfig.key, sortConfig.direction);
  }, [alerts, sortConfig]);

  const paginate = pageSize !== 0;
  const pageSizeActive = resolvePageSizeActive(paginate, pageSize);

  const [currentPage, setCurrentPage] = useState(1);

  useEffect(() => {
    setCurrentPage(1);
  }, [paginationResetKey]);

  const pageCount = useMemo(() => {
    if (!paginate) return 1;
    return Math.max(1, Math.ceil(sortedAlerts.length / pageSizeActive));
  }, [sortedAlerts.length, pageSizeActive, paginate]);

  useEffect(() => {
    setCurrentPage((p) => (p < 1 || p > pageCount ? 1 : p));
  }, [pageCount]);

  const displayedAlerts = useMemo(() => {
    if (!paginate) return sortedAlerts;
    const start = (currentPage - 1) * pageSizeActive;
    return sortedAlerts.slice(start, start + pageSizeActive);
  }, [sortedAlerts, currentPage, pageSizeActive, paginate]);

  const paginationPageItems = useMemo(
    () => (paginate && pageCount > 1 ? getPaginationPageItems(currentPage, pageCount) : []),
    [paginate, pageCount, currentPage],
  );

  const isLastPage = pageCount >= 1 && currentPage === pageCount;

  const handleLoadMoreClick = useCallback(() => {
    if (!onLoadMore) return;
    void onLoadMore();
  }, [onLoadMore]);

  const prevLoadMoreCompletionRef = useRef(-1);
  useEffect(() => {
    if (loadMoreCompletionCount === prevLoadMoreCompletionRef.current) return;
    prevLoadMoreCompletionRef.current = loadMoreCompletionCount;
    if (loadMoreCompletionCount === 0) return;
    setExpandedRows(new Set());
  }, [loadMoreCompletionCount]);

  useLayoutEffect(() => {
    const el = toolbarRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const measure = () => setStickyToolbarHeightPx(Math.ceil(el.getBoundingClientRect().height));
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [sortedAlerts.length, paginate, pageCount, currentPage, canLoadMore, isDark, loadingMore]);

  const thClass = `text-left py-3 px-4 text-xs uppercase tracking-wider ${
    isDark ? 'text-neutral-300 font-normal' : 'text-gray-600 font-semibold'
  }`;
  const tdTextClass = `py-3 px-4 text-sm ${isDark ? 'text-neutral-300' : 'text-gray-600'}`;
  const sortableThExtras = isDark ? 'hover:bg-neutral-800' : 'hover:bg-gray-200';
  const timeFormatUtc = timeFormat === 'utc';

  if (loading && alerts.length === 0) {
    return <AlertsTableLoadingView isDark={isDark} />;
  }

  if (error) {
    return (
      <AlertsTableErrorView
        isDark={isDark}
        error={error}
        onRefresh={onRefresh}
      />
    );
  }

  if (alerts.length === 0) {
    return <AlertsTableEmptyView isDark={isDark} />;
  }

  const theadClass = isDark ? 'bg-black border-neutral-700' : 'bg-gray-100 border-gray-300';
  const loadMoreFooterClass = isDark ? 'border-neutral-700 bg-black' : 'border-gray-300 bg-white';
  const showLoadMore = Boolean(
    canLoadMore &&
      isLastPage &&
      onLoadMore &&
      loadMoreBatchSize != null &&
      loadMoreBatchSize > 0,
  );

  return (
    <AlertsTableContent
      toolbarRef={toolbarRef}
      isDark={isDark}
      stickyToolbarHeightPx={stickyToolbarHeightPx}
      sortedAlerts={sortedAlerts}
      paginate={paginate}
      pageCount={pageCount}
      currentPage={currentPage}
      pageSizeActive={pageSizeActive}
      paginationPageItems={paginationPageItems}
      setCurrentPage={setCurrentPage}
      onPageSizeChange={onPageSizeChange}
      timeFormat={timeFormat}
      onTimeFormatChange={onTimeFormatChange}
      theadClass={theadClass}
      thClass={thClass}
      tdTextClass={tdTextClass}
      sortableThExtras={sortableThExtras}
      sortConfig={sortConfig}
      handleSort={handleSort}
      displayedAlerts={displayedAlerts}
      expandedRows={expandedRows}
      timeFormatUtc={timeFormatUtc}
      activeFilters={activeFilters}
      loadingAlertId={loadingAlertId}
      vstApiUrl={vstApiUrl}
      sensorMap={sensorMap}
      showObjectsBbox={showObjectsBbox}
      alertReportPromptTemplate={alertReportPromptTemplate}
      toggleRow={toggleRow}
      onAddFilter={onAddFilter}
      onPlayVideo={onPlayVideo}
      showLoadMore={showLoadMore ?? false}
      loadMoreFooterClass={loadMoreFooterClass}
      loadingMore={loadingMore}
      handleLoadMoreClick={handleLoadMoreClick}
      loadMoreBatchSize={loadMoreBatchSize}
      autoRefreshEnabled={autoRefreshEnabled}
    />
  );
}
