// SPDX-License-Identifier: MIT
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { CustomProvider, Button } from 'rsuite';
import { Select, Tag as KaizenTag } from '@nvidia/foundations-react-core';
import { IconChevronDown, IconChevronUp, IconX } from '@tabler/icons-react';
import { Funnel as FunnelIcon } from '@rsuite/icons';
import { FilterDialog } from './FilterPopover';
import { StreamInfo, FilterTag } from '../types';

export interface SearchSidebarControlsProps {
  theme: 'light' | 'dark';
  streams: StreamInfo[];
  filterParams: any;
  setFilterParams: (params: any) => void;
  addFilter: (params?: any) => void;
  removeFilterTag: (tag: FilterTag | null) => void;
  filterTags: FilterTag[];
  contentDisabled?: boolean;
  isDark: boolean;
  compactLayout?: boolean;
  outerPadding?: React.CSSProperties['padding'];
}

const SOURCE_TYPE_OPTIONS = [
  { label: 'Video', value: 'video_file' },
  { label: 'RTSP', value: 'rtsp' }
];

const SOURCE_TYPE_STORAGE_KEY = 'vss_search_sourceType';
const VALID_SOURCE_TYPES = new Set<string>(['video_file', 'rtsp']);

function getOnlyOneSourceType(streams: StreamInfo[]): 'video_file' | 'rtsp' | null {
  const hasVideoFile = streams.some((s) => s.type === 'sensor_file');
  const hasRtsp = streams.some((s) => s.type === 'sensor_rtsp');
  if (hasVideoFile && !hasRtsp) return 'video_file';
  if (!hasVideoFile && hasRtsp) return 'rtsp';
  return null;
}

function getStoredSourceType(): string | null {
  try {
    const stored = sessionStorage.getItem(SOURCE_TYPE_STORAGE_KEY);
    return stored && VALID_SOURCE_TYPES.has(stored) ? stored : null;
  } catch {
    return null;
  }
}

export const SearchSidebarControls: React.FC<SearchSidebarControlsProps> = ({
  theme,
  streams,
  filterParams,
  setFilterParams,
  addFilter,
  removeFilterTag,
  filterTags,
  contentDisabled = false,
  isDark,
  compactLayout = true,
  outerPadding = '8px 12px 12px',
}) => {
  const [mounted, setMounted] = useState(false);
  const [isPopoverOpen, setIsPopoverOpen] = useState(false);
  const [sourceType, setSourceType] = useState<string>(() => {
    const stored = getStoredSourceType();
    return stored ?? filterParams.sourceType ?? 'video_file';
  });
  const videoSourcesPerTypeRef = useRef<Record<string, string[]>>({
    video_file: [],
    rtsp: []
  });
  const filterParamsRef = useRef(filterParams);
  filterParamsRef.current = filterParams;
  const streamsRef = useRef(streams);
  streamsRef.current = streams;
  const initialSourceTypeRef = useRef<string | null>(null);
  if (initialSourceTypeRef.current === null) {
    initialSourceTypeRef.current = getStoredSourceType() ?? filterParams.sourceType ?? 'video_file';
  }

  useEffect(() => {
    if (getStoredSourceType() != null) return;
    const next = getOnlyOneSourceType(streams);
    if (next == null) return;
    if (sourceType === next) return;
    setSourceType(next);
    setFilterParams((prev: any) => ({ ...prev, sourceType: next }));
    try {
      sessionStorage.setItem(SOURCE_TYPE_STORAGE_KEY, next);
    } catch {
      // ignore
    }
  }, [streams, sourceType, setFilterParams]);

  useEffect(() => { setMounted(true); }, []);

  const didSyncSourceTypeRef = useRef(false);
  useEffect(() => {
    if (didSyncSourceTypeRef.current) return;
    didSyncSourceTypeRef.current = true;
    const initial = initialSourceTypeRef.current;
    const current = filterParamsRef.current;
    if (initial == null) return;
    if (getOnlyOneSourceType(streamsRef.current) != null) return;
    if (current.sourceType !== initial) {
      setFilterParams({ ...current, sourceType: initial });
    }
  }, [setFilterParams]);

  const close = useCallback(() => setIsPopoverOpen(false), []);
  const togglePopover = useCallback(() => setIsPopoverOpen((prev) => !prev), []);

  useEffect(() => {
    if (contentDisabled) setIsPopoverOpen(false);
  }, [contentDisabled]);

  const tagResetValues: Record<string, any> = useMemo(() => ({
    startDate: { startDate: null },
    endDate: { endDate: null },
    videoSources: { videoSources: [] },
    similarity: { similarity: '' },
  }), []);

  const handleSourceTypeChange = useCallback((value: string | null) => {
    if (value && value !== sourceType) {
      try {
        sessionStorage.setItem(SOURCE_TYPE_STORAGE_KEY, value);
      } catch {
        // ignore
      }
      videoSourcesPerTypeRef.current[sourceType] = filterParams.videoSources || [];
      const savedVideoSources = videoSourcesPerTypeRef.current[value] || [];

      setSourceType(value);
      const newParams = { ...filterParams, sourceType: value, videoSources: savedVideoSources };
      setFilterParams(newParams);

      const videoSourcesTag = filterTags.find((tag: FilterTag) => tag.key === 'videoSources');
      if (videoSourcesTag && savedVideoSources.length === 0) {
        removeFilterTag(videoSourcesTag);
      } else if (savedVideoSources.length > 0) {
        addFilter(newParams);
      }
    }
  }, [sourceType, filterParams, filterTags, setFilterParams, removeFilterTag, addFilter]);

  const handleConfirm = useCallback((newParams?: any) => {
    const paramsToUse = newParams || filterParams;
    if (newParams) {
      setFilterParams(newParams);
    }
    addFilter(paramsToUse);
  }, [filterParams, setFilterParams, addFilter]);

  const removeTag = useCallback((tag: FilterTag) => {
    const resetValue = tagResetValues[tag.key] || {};
    const newParams = { ...filterParams, ...resetValue };

    setFilterParams(newParams);
    removeFilterTag(tag);
  }, [filterParams, tagResetValues, setFilterParams, removeFilterTag]);

  const onClearAll = useCallback(() => {
    const newParams = { ...filterParams, startDate: null, endDate: null, videoSources: [], similarity: 0 };
    removeFilterTag(null);
    setFilterParams(newParams);
  }, [filterParams, removeFilterTag, setFilterParams]);

  const visibleTags = useMemo(
    () => filterTags.filter((tag: FilterTag) => tag.key !== 'topK'),
    [filterTags]
  );

  return (
    <CustomProvider theme={theme}>
      <div
        style={{
          display: 'flex',
          flexDirection: 'column',
          gap: 12,
          padding: outerPadding,
          boxSizing: 'border-box',
          width: '100%',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
          <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Source Type</span>
          <div style={{ width: compactLayout ? '100%' : 260 }}>
            {mounted && (
              <Select
                data-testid="search-source-type"
                value={sourceType}
                onValueChange={(val) => handleSourceTypeChange(val)}
                disabled={contentDisabled}
                items={SOURCE_TYPE_OPTIONS.map((opt) => ({
                  value: opt.value,
                  children: opt.label,
                }))}
              />
            )}
          </div>
        </div>

        <div
          className={`flex flex-col rounded-lg border ${
            isDark ? 'border-gray-700 bg-neutral-900' : 'border-gray-300 bg-white'
          }`}
        >
          <button
            type="button"
            data-testid="search-filter-button"
            onClick={togglePopover}
            disabled={contentDisabled}
            aria-expanded={isPopoverOpen}
            aria-controls="search-filter-expand-content"
            className={`w-full px-3.5 py-2.5 min-h-[44px] flex items-center gap-2 text-left transition-colors ${
              isDark ? 'text-gray-100 hover:bg-neutral-800' : 'text-gray-800 hover:bg-gray-50'
            }`}
          >
            {isPopoverOpen ? <IconChevronUp size={16} /> : <IconChevronDown size={16} />}
            <FunnelIcon />
            <span>Filters</span>
          </button>
          <div
            id="search-filter-expand-content"
            style={{
              borderTop: isPopoverOpen ? `1px solid ${theme === 'dark' ? '#3c3f43' : '#e5e7eb'}` : 'none',
            }}
          >
            <FilterDialog
              isOpen={isPopoverOpen}
              isDark={isDark}
              disabled={contentDisabled}
              handleConfirm={handleConfirm}
              close={close}
              streams={streams}
              filterParams={filterParams}
              setFilterParams={setFilterParams}
              sourceType={sourceType}
              inline
            />
          </div>
        </div>

        {visibleTags.length > 0 && (
          <div
            data-testid="search-filter-tags"
            style={{
              display: 'flex',
              flexWrap: 'wrap',
              gap: 6,
              alignItems: 'center',
              pointerEvents: contentDisabled ? 'none' : 'auto',
            }}
          >
            {visibleTags.map((tag: FilterTag, index: number) => (
              <KaizenTag
                key={tag.key ?? index}
                kind="outline"
                color="gray"
                readOnly={contentDisabled}
                style={{ opacity: contentDisabled ? 0.5 : 1 }}
                onClick={!contentDisabled ? () => removeTag(tag) : undefined}
              >
                {tag.title}: <span style={{ color: theme === 'dark' ? '#84E1BC' : 'green' }}>{tag.value}</span>
                {!contentDisabled && <IconX size={14} />}
              </KaizenTag>
            ))}
            {visibleTags.length > 0 && (
              <Button data-testid="search-clear-all-filters" size="sm" appearance="primary" color="red" onClick={onClearAll} disabled={contentDisabled}>
                Clear All
              </Button>
            )}
          </div>
        )}
      </div>
    </CustomProvider>
  );
};
