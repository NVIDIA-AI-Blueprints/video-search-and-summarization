// SPDX-License-Identifier: MIT
import React, { useMemo, useCallback, useState, useEffect, useLayoutEffect } from 'react';
import { createPortal } from 'react-dom';
import { Stack, DatePicker, CheckPicker, NumberInput } from 'rsuite';
import { DEFAULT_TOP_K } from '../hooks/useFilter';
import { StreamInfo } from '../types';

const FILTER_POPOVER_Z_INDEX = 10600;

interface FilterDialogProps {
  isOpen: boolean;
  isDark: boolean;
  handleConfirm: (params?: any) => void;
  close: () => void;
  streams: StreamInfo[];
  filterParams: any;
  setFilterParams: (params: any) => void;
  containerRef?: React.RefObject<HTMLDivElement>;
  /** Ref to the trigger (Filter button) for positioning when using portal; ensures popover appears above Chat sidebar. */
  triggerRef?: React.RefObject<HTMLDivElement | null>;
  /** When true, filter inputs are disabled (e.g. when Chat sidebar is open or query is running). */
  disabled?: boolean;
  sourceType?: string;
}

// Common button styles
const baseButtonStyle: React.CSSProperties = {
  fontSize: 14,
  fontWeight: 500,
  borderRadius: 6,
  border: 'none',
  cursor: 'pointer',
  transition: 'background-color 0.2s',
};

export const FilterDialog: React.FC<FilterDialogProps> = ({
  isOpen,
  isDark,
  handleConfirm,
  close,
  streams,
  filterParams,
  setFilterParams,
  containerRef,
  triggerRef,
  disabled = false,
  sourceType = 'video_file',
}) => {
  const [pendingParams, setPendingParams] = useState(filterParams);
  const [wasOpen, setWasOpen] = useState(isOpen);
  const [portalPosition, setPortalPosition] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    if (isOpen && !wasOpen) {
      setPendingParams(filterParams);
    } else if (!isOpen && wasOpen) {
      setPendingParams(filterParams);
    }
    setWasOpen(isOpen);
  }, [isOpen, wasOpen, filterParams]);

  // Position for portal: below trigger, so popover is not hidden behind Chat sidebar
  const updatePortalPosition = useCallback(() => {
    if (!triggerRef?.current) return;
    const rect = triggerRef.current.getBoundingClientRect();
    setPortalPosition({ top: rect.bottom + 8, left: rect.left });
  }, [triggerRef]);

  useLayoutEffect(() => {
    if (!isOpen || !triggerRef) return;
    updatePortalPosition();
  }, [isOpen, triggerRef, updatePortalPosition]);

  useEffect(() => {
    if (!isOpen || !triggerRef) return;
    const onScrollOrResize = () => updatePortalPosition();
    window.addEventListener('scroll', onScrollOrResize, true);
    window.addEventListener('resize', onScrollOrResize);
    return () => {
      window.removeEventListener('scroll', onScrollOrResize, true);
      window.removeEventListener('resize', onScrollOrResize);
    };
  }, [isOpen, triggerRef, updatePortalPosition]);
  
  const { startDate, endDate, videoSources, similarity, topK } = pendingParams;

  // Filter streams based on sourceType
  const filteredStreams = useMemo(() => {
    const targetType = sourceType === 'video_file' ? 'sensor_file' : 'sensor_rtsp';
    return streams.filter(stream => stream.type === targetType);
  }, [streams, sourceType]);
  
  const labelStyle: React.CSSProperties = useMemo(() => ({ 
    width: 70, textAlign: 'right', flexShrink: 0 
  }), []);
  const inputStyle = useMemo(() => ({ width: 230 }), []);

  // Memoized handlers - update local pending state only
  const handleStartDateChange = useCallback((value: Date | null) => 
    setPendingParams((prev: any) => ({ ...prev, startDate: value })), []);
  const handleEndDateChange = useCallback((value: Date | null) => 
    setPendingParams((prev: any) => ({ ...prev, endDate: value })), []);
  const handleSimilarityChange = useCallback((value: string | number | null) => 
    setPendingParams((prev: any) => ({ ...prev, similarity: value })), []);
  const handleVideoSourcesChange = useCallback((value: string[]) => 
    setPendingParams((prev: any) => ({ ...prev, videoSources: value })), []);
  const handleTopKChange = useCallback((value: string | number | null) => 
    setPendingParams((prev: any) => ({ ...prev, topK: value })), []);

  const handleApply = useCallback(() => {
    handleConfirm(pendingParams);
  }, [pendingParams, handleConfirm]);

  const handleCancel = useCallback(() => {
    setPendingParams(filterParams);
    close();
  }, [filterParams, close]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleApply();
    }
  }, [handleApply]);

  if (!isOpen) return null;

  const usePortal = Boolean(triggerRef && portalPosition !== null);
  if (triggerRef && portalPosition === null) return null;
  const popoverContent = (
    <div
      ref={containerRef}
      onKeyDown={handleKeyDown}
      style={{
        position: usePortal ? 'fixed' : 'absolute',
        ...(usePortal && portalPosition
          ? { top: portalPosition.top, left: portalPosition.left }
          : { top: '100%', left: 0, marginTop: 8 }),
        padding: 12,
        borderRadius: 6,
        boxShadow: '0 4px 12px rgba(0, 0, 0, 0.15)',
        border: `1px solid ${isDark ? '#3c3f43' : '#e5e5ea'}`,
        backgroundColor: isDark ? '#1a1d24' : '#fff',
        zIndex: usePortal ? FILTER_POPOVER_Z_INDEX : 1050,
        minWidth: 350,
      }}
    >
      <Stack direction="column" spacing={12}>
        <Stack spacing={10} alignItems="center">
          <span style={labelStyle}>From:</span>
          <DatePicker
            disabled={disabled}
            format="MMM dd yyyy hh:mm:ss aa"
            showMeridiem
            value={startDate}
            onChange={handleStartDateChange}
            onSelect={handleStartDateChange}
            onChangeCalendarDate={handleStartDateChange}
            style={inputStyle}
            hideSeconds={(second) => second % 10 !== 0}
            hideMinutes={(minute) => minute % 5 !== 0}
            placeholder="From"
          />
        </Stack>
        <Stack spacing={10} alignItems="center">
          <span style={labelStyle}>To:</span>
          <DatePicker
            disabled={disabled}
            format="MMM dd yyyy hh:mm:ss aa"
            showMeridiem
            value={endDate}
            onChange={handleEndDateChange}
            onSelect={handleEndDateChange}
            onChangeCalendarDate={handleEndDateChange}
            style={inputStyle}
            hideSeconds={(second) => second % 10 !== 0}
            hideMinutes={(minute) => minute % 5 !== 0}
            placeholder="To"
          />
        </Stack>
        <Stack spacing={10} alignItems="center">
          <span style={labelStyle}>Video sources:</span>
          <div style={inputStyle}>
            <CheckPicker
              value={videoSources}
              onChange={handleVideoSourcesChange}
              data={filteredStreams.map((stream) => ({ label: stream.name, value: stream.name }))}
              searchable={false}
              placeholder="Video sources"
              block
              disabled={disabled}
            />
          </div>
        </Stack>
        <Stack spacing={10} alignItems="center">
          <span style={labelStyle}>Min Cosine Similarity:</span>
          <NumberInput
            disabled={disabled}
            formatter={(value: string | number) => {
              const num = Number(value);
              return isNaN(num) ? '' : num.toFixed(2);
            }}
            min={-1}
            max={1}
            step={0.01}
            value={similarity}
            onChange={handleSimilarityChange}
            placeholder="Min Cosine Similarity"
            style={inputStyle}
          />
        </Stack>
        <Stack spacing={10} alignItems="center">
          <span style={labelStyle}>
            <span style={{ color: 'red' }}>*</span> Show top K Results:
          </span>
          <NumberInput
            min={1}
            step={1}
            value={topK}
            disabled={disabled}
            onBlur={(e) => {
              const value = (e.target as HTMLInputElement)?.value;
              if (!value) {
                setPendingParams((prev: any) => ({ ...prev, topK: DEFAULT_TOP_K }));
              }
            }}
            onChange={handleTopKChange}
            placeholder="Number of results"
            style={inputStyle}
          />
        </Stack>
      </Stack>
      {/* Footer */}
      <div style={{
        marginTop: 15,
        paddingTop: 12,
        borderTop: `1px solid ${isDark ? '#3c3f43' : '#e5e5ea'}`,
        display: 'flex',
        justifyContent: 'flex-end',
        gap: 8,
      }}>
        <button
          onClick={handleCancel}
          style={{
            ...baseButtonStyle,
            padding: '8px 12px',
            backgroundColor: 'transparent',
            color: isDark ? '#d1d5db' : '#374151',
          }}
          onMouseEnter={(e) => e.currentTarget.style.backgroundColor = isDark ? '#374151' : '#f3f4f6'}
          onMouseLeave={(e) => e.currentTarget.style.backgroundColor = 'transparent'}
        >
          Cancel
        </button>
        <button
          onClick={handleApply}
          style={{
            ...baseButtonStyle,
            padding: '8px 16px',
            backgroundColor: isDark ? '#0891b2' : '#2563eb',
            color: '#fff',
          }}
          onMouseEnter={(e) => e.currentTarget.style.backgroundColor = isDark ? '#0e7490' : '#1d4ed8'}
          onMouseLeave={(e) => e.currentTarget.style.backgroundColor = isDark ? '#0891b2' : '#2563eb'}
        >
          Apply
        </button>
      </div>
    </div>
  );

  if (usePortal && typeof document !== 'undefined') {
    return createPortal(popoverContent, document.body);
  }
  return popoverContent;
};
