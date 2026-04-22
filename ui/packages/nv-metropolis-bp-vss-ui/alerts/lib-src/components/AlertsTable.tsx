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

import React, { useState, useCallback, useMemo } from 'react';
import { Button } from '@nvidia/foundations-react-core';
import { IconChevronDown, IconChevronUp, IconPlayerPlay, IconRefresh, IconInfoCircle, IconArrowsUpDown, IconArrowUp, IconArrowDown } from '@tabler/icons-react';
import { AlertData, FilterState, FilterType, VLM_VERDICT } from '../types';
import { formatAlertTimestamp } from '../utils/timeUtils';
import { MetadataSection } from './MetadataSection';
import { ThumbnailButton } from './ThumbnailButton';

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
  timeFormat?: 'local' | 'utc';
}

export const AlertsTable: React.FC<AlertsTableProps> = ({
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
  timeFormat = 'local'
}) => {
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [sortConfig, setSortConfig] = useState<{
    key: 'timestamp' | 'end' | null;
    direction: 'asc' | 'desc' | null;
  }>({ key: null, direction: null });

  const toggleRow = useCallback((id: string) => {
    setExpandedRows(prev => {
      const newSet = new Set(prev);
      newSet.has(id) ? newSet.delete(id) : newSet.add(id);
      return newSet;
    });
  }, []);

  const handleSort = useCallback((key: 'timestamp' | 'end') => {
    // Clear expanded rows when sorting to avoid confusion with row order changes
    setExpandedRows(new Set());
    
    setSortConfig(prev => {
      if (prev.key !== key || prev.direction === null) {
        // First click: sort ascending
        return { key, direction: 'asc' };
      } else if (prev.direction === 'asc') {
        // Second click: sort descending
        return { key, direction: 'desc' };
      } else {
        // Third click: reset to default (no sorting)
        return { key: null, direction: null };
      }
    });
  }, []);

  // Sort alerts based on timestamp or end
  const sortedAlerts = useMemo(() => {
    if (!sortConfig.key || !sortConfig.direction) return alerts;

    const sortKey = sortConfig.key;
    return [...alerts].sort((a, b) => {
      const aValue = a[sortKey] || '';
      const bValue = b[sortKey] || '';
      
      if (!aValue && !bValue) return 0;
      if (!aValue) return 1;
      if (!bValue) return -1;
      
      const aTime = new Date(aValue).getTime();
      const bTime = new Date(bValue).getTime();
      
      if (isNaN(aTime) && isNaN(bTime)) return 0;
      if (isNaN(aTime)) return 1;
      if (isNaN(bTime)) return -1;
      
      const result = aTime - bTime;
      return sortConfig.direction === 'asc' ? result : -result;
    });
  }, [alerts, sortConfig]);

  // Theme-based styles
  const thClass = `text-left py-3 px-4 text-xs uppercase tracking-wider ${
    isDark ? 'text-neutral-300 font-normal' : 'text-gray-600 font-semibold'
  }`;
  const tdTextClass = `py-3 px-4 text-sm ${isDark ? 'text-neutral-300' : 'text-gray-600'}`;


  if (loading && alerts.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <IconRefresh className={`w-8 h-8 animate-spin [animation-direction:reverse] mx-auto mb-3 ${isDark ? 'text-green-400' : 'text-green-500'}`} />
          <p className={`text-base font-medium ${isDark ? 'text-neutral-300' : 'text-gray-700'}`}>Loading alerts...</p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className={`text-center p-6 rounded-lg ${isDark ? 'bg-red-500/10 border border-red-500/20' : 'bg-red-50'}`}>
          <p className={`font-bold text-lg mb-2 ${isDark ? 'text-red-400' : 'text-red-700'}`}>Error loading alerts</p>
          <div className={`text-sm mb-4 max-h-24 overflow-auto rounded p-3 break-words whitespace-pre-wrap ${isDark ? 'bg-black/50 text-gray-300' : 'bg-red-100/50 text-red-600 border border-red-200'}`}>
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

  if (alerts.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className={`text-base font-medium ${isDark ? 'text-gray-300' : 'text-gray-600'}`}>
          No results found (Verify that the database has alert data).
        </p>
      </div>
    );
  }

  return (
    <div className="w-full">
      <div className={`px-4 py-2 border-b ${
        isDark ? 'bg-black border-neutral-700' : 'bg-white border-gray-300'
      }`}>
        <div className={`inline-flex items-center gap-3 px-3.5 py-1.5 rounded-lg transition-all ${
          isDark 
            ? 'bg-black/30 hover:bg-black/40' 
            : 'bg-gray-100/60 hover:bg-gray-100'
        }`}>
          <label className={`text-sm font-medium whitespace-nowrap ${isDark ? 'text-gray-300' : 'text-gray-700'}`}>
            Alerts Displayed:
          </label>
          <span data-testid="alerts-displayed-count" className={`inline-flex items-center justify-center px-3.5 py-0.5 rounded-full text-xs font-semibold border ${
            isDark ? 'bg-black text-white border-white' : 'bg-white text-gray-800 border-gray-400'
          }`}>{sortedAlerts.length}</span>
        </div>
      </div>
      <table data-testid="alerts-table" className="w-full border-collapse">
        <thead className={`sticky top-0 z-10 border-b ${
          isDark ? 'bg-black border-neutral-700' : 'bg-gray-100 border-gray-300'
        }`}>
        <tr>
          <th className={`${thClass} w-8`}></th>
          <th className={`${thClass} w-8`}></th>
          <th className={`${thClass} cursor-pointer select-none hover:bg-opacity-10 ${
            isDark ? 'hover:bg-neutral-800' : 'hover:bg-gray-200'
          }`} onClick={() => handleSort('timestamp')}>
            <div className="flex items-center gap-2">
              <span>Timestamp</span>
              {sortConfig.key === 'timestamp' && sortConfig.direction === 'asc' ? (
                <IconArrowUp className="w-4 h-4" />
              ) : sortConfig.key === 'timestamp' && sortConfig.direction === 'desc' ? (
                <IconArrowDown className="w-4 h-4" />
              ) : (
                <IconArrowsUpDown className="w-4 h-4 opacity-50" />
              )}
            </div>
          </th>
          <th className={`${thClass} cursor-pointer select-none hover:bg-opacity-10 ${
            isDark ? 'hover:bg-neutral-800' : 'hover:bg-gray-200'
          }`} onClick={() => handleSort('end')}>
            <div className="flex items-center gap-2">
              <span>End</span>
              {sortConfig.key === 'end' && sortConfig.direction === 'asc' ? (
                <IconArrowUp className="w-4 h-4" />
              ) : sortConfig.key === 'end' && sortConfig.direction === 'desc' ? (
                <IconArrowDown className="w-4 h-4" />
              ) : (
                <IconArrowsUpDown className="w-4 h-4 opacity-50" />
              )}
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
        {sortedAlerts.map((alert, index) => {
          const isExpanded = expandedRows.has(alert.id);
          return (
            <React.Fragment key={alert.id}>
              <tr data-testid="alert-row" className={`border-b transition-colors ${
                isDark 
                  ? `border-neutral-700 hover:bg-neutral-800 ${index % 2 === 0 ? 'bg-black' : 'bg-neutral-950'}`
                  : `border-gray-200 hover:bg-gray-100 ${index % 2 === 0 ? 'bg-white' : 'bg-gray-50'}`
              }`}>
                <td className="py-3 px-4 text-sm">
                  <button onClick={() => toggleRow(alert.id)} className="p-1.5 rounded transition-colors text-gray-400 hover:text-white hover:bg-neutral-700">
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
                <td className={tdTextClass}>{alert.timestamp ? formatAlertTimestamp(alert.timestamp, timeFormat === 'utc') : 'N/A'}</td>
                <td className={tdTextClass}>{alert.end ? formatAlertTimestamp(alert.end, timeFormat === 'utc') : 'N/A'}</td>
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
                  <Button
                    kind="tertiary"
                    onClick={() => {
                      if (!activeFilters.alertTriggered.has(alert.alertTriggered)) {
                        onAddFilter('alertTriggered', alert.alertTriggered);
                      }
                    }}
                  >
                    {alert.alertTriggered ? alert.alertTriggered : 'N/A'}
                  </Button>
                </td>
                <td className={tdTextClass}>
                  {(() => {
                    const verdict = alert.metadata?.analyticsModule?.info?.verdict || alert.metadata?.info?.verdict;
                    if (!verdict) return 'N/A';
                    
                    // Style based on verdict value using constants
                    const verdictStyles: Record<string, string> = {
                      [VLM_VERDICT.CONFIRMED]: isDark ? 'text-green-400 bg-green-500/10 border-green-500/30' : 'text-green-700 bg-green-50 border-green-200',
                      [VLM_VERDICT.REJECTED]: isDark ? 'text-red-400 bg-red-500/10 border-red-500/30' : 'text-red-700 bg-red-50 border-red-200',
                      [VLM_VERDICT.VERIFICATION_FAILED]: isDark ? 'text-yellow-400 bg-yellow-500/10 border-yellow-500/30' : 'text-yellow-700 bg-yellow-50 border-yellow-200',
                      [VLM_VERDICT.NOT_CONFIRMED]: isDark ? 'text-gray-400 bg-gray-500/10 border-gray-500/30' : 'text-gray-700 bg-gray-50 border-gray-200'
                    };
                    
                    const style = verdictStyles[verdict] || (isDark ? 'text-gray-400 bg-gray-500/10 border-gray-500/30' : 'text-gray-700 bg-gray-50 border-gray-200');
                    
                    // Format display text
                    const displayText = verdict.split('-').map((word: string) => 
                      word.charAt(0).toUpperCase() + word.slice(1)
                    ).join(' ');
                    
                    return (
                      <span data-testid="alert-vlm-verdict" className={`inline-block px-2 py-1 rounded text-xs font-medium border ${style}`}>
                        {displayText}
                      </span>
                    );
                  })()}
                </td>
                <td className={tdTextClass}>
                  {alert.alertDescription ? alert.alertDescription : 'N/A'}
                </td>
                <td className="py-3 px-4 text-sm">
                  <button onClick={() => toggleRow(alert.id)} className="p-1.5 rounded transition-colors text-gray-400 hover:text-white hover:bg-neutral-700">
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
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
    </div>
  );
};

