// SPDX-License-Identifier: MIT
import React, { useMemo, useCallback, useState, useEffect, useLayoutEffect, useRef } from 'react';
import { Button } from '@nvidia/foundations-react-core';
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
  containerRef?: React.RefObject<HTMLDivElement | null>;
  /** Ref to the trigger (Filter button) for positioning when using portal; ensures popover appears above Chat sidebar. */
  triggerRef?: React.RefObject<HTMLDivElement | null>;
  /** When true, filter inputs are disabled (e.g. when Chat sidebar is open or query is running). */
  disabled?: boolean;
  sourceType?: string;
  /** Render filters inline (sidebar) instead of as a popover. */
  inline?: boolean;
}

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
  inline = false,
}) => {
  const [pendingParams, setPendingParams] = useState(filterParams);
  const [wasOpen, setWasOpen] = useState(isOpen);
  const [portalPosition, setPortalPosition] = useState<{ top: number; left: number } | null>(null);

  useEffect(() => {
    if (isOpen !== wasOpen) {
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
  
  const labelStyle: React.CSSProperties = useMemo(() => (
    inline
      ? { width: '100%', textAlign: 'left', flexShrink: 0 }
      : { width: 70, textAlign: 'right', flexShrink: 0 }
  ), [inline]);
  const inputStyle = useMemo(() => (
    inline
      ? { width: '100%', minWidth: 0 }
      : { width: 230 }
  ), [inline]);

  const updatePendingParams = useCallback(
    (patch: Record<string, unknown>) => {
      setPendingParams((prev: any) => {
        const next = { ...prev, ...patch };
        if (inline) {
          setFilterParams(next);
          handleConfirm(next);
        }
        return next;
      });
    },
    [inline, setFilterParams, handleConfirm],
  );

  const handleStartDateChange = useCallback((value: Date | null) =>
    updatePendingParams({ startDate: value }), [updatePendingParams]);
  const handleEndDateChange = useCallback((value: Date | null) =>
    updatePendingParams({ endDate: value }), [updatePendingParams]);
  const handleSimilarityChange = useCallback((value: string | number | null) =>
    updatePendingParams({ similarity: value }), [updatePendingParams]);
  const handleVideoSourcesChange = useCallback((value: string[]) =>
    updatePendingParams({ videoSources: value }), [updatePendingParams]);
  const handleTopKChange = useCallback((value: string | number | null) =>
    updatePendingParams({ topK: value }), [updatePendingParams]);

  const handleApply = useCallback(() => {
    handleConfirm(pendingParams);
  }, [pendingParams, handleConfirm]);

  const handleCancel = useCallback(() => {
    setPendingParams(filterParams);
    close();
  }, [filterParams, close]);

  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = popoverRef.current;
    if (!el) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Enter') {
        e.preventDefault();
        handleApply();
      }
    };
    el.addEventListener('keydown', handleKeyDown);
    return () => el.removeEventListener('keydown', handleKeyDown);
  }, [handleApply]);

  if (!isOpen) return null;

  const usePortal = !inline && Boolean(triggerRef && portalPosition !== null);
  if (!inline && triggerRef && portalPosition === null) return null;
  const popoverContent = (
    <div
      data-testid="search-filter-dialog"
      ref={(el) => {
        (popoverRef as React.MutableRefObject<HTMLDivElement | null>).current = el;
        if (containerRef) (containerRef as React.MutableRefObject<HTMLDivElement | null>).current = el;
      }}
      style={{
        position: inline ? 'relative' : (usePortal ? 'fixed' : 'absolute'),
        ...(inline
          ? {}
          : usePortal && portalPosition
            ? { top: portalPosition.top, left: portalPosition.left }
            : { top: '100%', left: 0, marginTop: 8 }),
        padding: 12,
        borderRadius: inline ? 0 : 6,
        boxShadow: inline ? 'none' : '0 4px 12px rgba(0, 0, 0, 0.15)',
        border: inline ? 'none' : `1px solid ${isDark ? '#3c3f43' : '#e5e5ea'}`,
        backgroundColor: inline ? 'transparent' : (isDark ? '#1a1d24' : '#fff'),
        zIndex: inline ? 'auto' : (usePortal ? FILTER_POPOVER_Z_INDEX : 1050),
        width: inline ? '100%' : undefined,
        boxSizing: 'border-box',
        minWidth: inline ? 'auto' : 350,
      }}
    >
      <Stack direction="column" spacing={12} alignItems="stretch" style={{ width: '100%' }}>
        <Stack
          spacing={inline ? 6 : 10}
          alignItems={inline ? 'stretch' : 'center'}
          direction={inline ? 'column' : 'row'}
          style={inline ? { width: '100%' } : undefined}
        >
          <span style={labelStyle}>From:</span>
          <DatePicker
            data-testid="search-filter-from"
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
        <Stack spacing={inline ? 6 : 10} alignItems={inline ? 'stretch' : 'center'} direction={inline ? 'column' : 'row'}>
          <span style={labelStyle}>To:</span>
          <DatePicker
            data-testid="search-filter-to"
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
        <Stack spacing={inline ? 6 : 10} alignItems={inline ? 'stretch' : 'center'} direction={inline ? 'column' : 'row'}>
          <span style={labelStyle}>Video sources:</span>
          <div style={{ ...inputStyle, width: '100%', minWidth: 0 }}>
            <CheckPicker
              data-testid="search-filter-video-sources"
              value={videoSources}
              onChange={handleVideoSourcesChange}
              data={filteredStreams.map((stream) => ({ label: stream.name, value: stream.name }))}
              searchable={false}
              placeholder="Video sources"
              block
              disabled={disabled}
              style={{ width: '100%' }}
            />
          </div>
        </Stack>
        <Stack spacing={inline ? 6 : 10} alignItems={inline ? 'stretch' : 'center'} direction={inline ? 'column' : 'row'}>
          <span style={labelStyle}>Min Cosine Similarity:</span>
          <NumberInput
            data-testid="search-filter-similarity"
            disabled={disabled}
            formatter={(value: string | number) => {
              const num = Number(value);
              return Number.isNaN(num) ? '' : num.toFixed(2);
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
        <Stack spacing={inline ? 6 : 10} alignItems={inline ? 'stretch' : 'center'} direction={inline ? 'column' : 'row'}>
          <span style={labelStyle}>
            <span style={{ color: 'red' }}>*</span> Show top K Results:
          </span>
          <NumberInput
            data-testid="search-filter-topk"
            min={1}
            step={1}
            value={topK}
            disabled={disabled}
            onBlur={(e) => {
              const value = (e.target as HTMLInputElement)?.value;
              if (!value) {
                updatePendingParams({ topK: DEFAULT_TOP_K });
              }
            }}
            onChange={handleTopKChange}
            placeholder="Number of results"
            style={inputStyle}
          />
        </Stack>
      </Stack>
      {!inline && (
      <div style={{
        marginTop: 15,
        paddingTop: 12,
        borderTop: `1px solid ${isDark ? '#3c3f43' : '#e5e5ea'}`,
        display: 'flex',
        justifyContent: 'flex-end',
        gap: 8,
      }}>
        <Button
          data-testid="search-filter-cancel"
          kind="secondary"
          onClick={handleCancel}
        >
          Cancel
        </Button>
        <Button
          data-testid="search-filter-apply"
          kind="primary"
          onClick={handleApply}
        >
          Apply
        </Button>
      </div>
      )}
    </div>
  );

  if (usePortal && typeof document !== 'undefined') {
    return createPortal(popoverContent, document.body);
  }
  return popoverContent;
};
