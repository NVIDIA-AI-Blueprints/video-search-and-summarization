// SPDX-License-Identifier: MIT
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { CustomProvider, Whisper, Button, Tooltip } from 'rsuite';
import { Select, Button as KaizenButton, TextInput, Tag as KaizenTag } from '@nvidia/foundations-react-core';
import { IconX } from '@tabler/icons-react';
import { Search as SearchIcon, Funnel as FunnelIcon, Close as CloseIcon, InfoRound as InfoRoundIcon } from '@rsuite/icons';
import { IconRefresh } from '@tabler/icons-react';
import { FilterDialog } from './FilterPopover';
import { SearchParams, StreamInfo, FilterTag } from '../types';
import { DEFAULT_TOP_K } from '../hooks/useFilter';

interface SearchHeaderProps {
    onUpdateSearchParams: (params: SearchParams) => void;
    theme: 'light' | 'dark';    
    streams: StreamInfo[];
    filterParams: any;
    setFilterParams: (params: any) => void;
    addFilter: (params?: any) => void;
    removeFilterTag: (tag: FilterTag | null) => void;
    filterTags: FilterTag[];
    isSearching?: boolean;
    onCancelSearch?: () => void;
    onGetPendingQuery?: (getPendingFn: () => string) => void;
    submitChatMessage?: (message: string) => void;
    /** When true, disables search input, source type, filters, and tags (e.g. when Chat sidebar is open or query is running). */
    contentDisabled?: boolean;
  }

const SOURCE_TYPE_OPTIONS = [
    { label: 'Video', value: 'video_file' },
    { label: 'RTSP', value: 'rtsp' }
];

const SOURCE_TYPE_STORAGE_KEY = 'vss_search_sourceType';
const VALID_SOURCE_TYPES = new Set<string>(['video_file', 'rtsp']);

/** Returns 'video_file' | 'rtsp' when only that type has streams; null when both or neither. */
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

const SEARCH_HEADER_SPIN_STYLE_ID = 'search-header-spin-keyframes';
let searchHeaderSpinRefCount = 0;

export const SearchHeader: React.FC<SearchHeaderProps> = ({ onUpdateSearchParams, theme, streams, filterParams, setFilterParams, addFilter, removeFilterTag, filterTags, isSearching = false, onCancelSearch, onGetPendingQuery, submitChatMessage, contentDisabled = false }) => {
    const [mounted, setMounted] = useState(false);
    const [query, setQuery] = useState(filterParams.query || '');
    const [hasQueryError, setHasQueryError] = useState(false);
    const [isPopoverOpen, setIsPopoverOpen] = useState(false);
    const [sourceType, setSourceType] = useState<string>(() => {
        const stored = getStoredSourceType();
        return stored ?? filterParams.sourceType ?? 'video_file';
    });
    // Store videoSources separately for each sourceType (useRef to avoid re-renders)
    const videoSourcesPerTypeRef = useRef<Record<string, string[]>>({
        video_file: [],
        rtsp: []
    });
    const popoverRef = useRef<HTMLDivElement>(null);
    const filterButtonRef = useRef<HTMLDivElement>(null);
    const filterParamsRef = useRef(filterParams);
    filterParamsRef.current = filterParams;
    const streamsRef = useRef(streams);
    streamsRef.current = streams;
    const initialSourceTypeRef = useRef<string | null>(null);
    if (initialSourceTypeRef.current === null) {
        initialSourceTypeRef.current = getStoredSourceType() ?? filterParams.sourceType ?? 'video_file';
    }

    // Default Source Type only on first visit (no session storage): prefer the option that has video sources.
    // Once user has a stored preference, allow any option (including one with no streams) to avoid confusion.
    useEffect(() => {
        if (getStoredSourceType() != null) return;
        const next = getOnlyOneSourceType(streams);
        if (next == null) return; // both or neither → keep current selection
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

    // Inject keyframes once per document; remove when last instance unmounts (ref-count)
    useEffect(() => {
        searchHeaderSpinRefCount += 1;
        let style = document.getElementById(SEARCH_HEADER_SPIN_STYLE_ID) as HTMLStyleElement | null;
        if (!style) {
            style = document.createElement('style');
            style.id = SEARCH_HEADER_SPIN_STYLE_ID;
            style.textContent = '@keyframes searchHeaderSpin { to { transform: rotate(360deg); } }';
            document.head.appendChild(style);
        }
        return () => {
            searchHeaderSpinRefCount -= 1;
            if (searchHeaderSpinRefCount <= 0) {
                searchHeaderSpinRefCount = 0;
                document.getElementById(SEARCH_HEADER_SPIN_STYLE_ID)?.remove();
            }
        };
    }, []);

    // Sync restored sourceType to parent on mount only (use refs to avoid stale closure).
    // Skip when only one stream type exists so the "Default Source Type" effect handles it and we don't overwrite.
    useEffect(() => {
        const initial = initialSourceTypeRef.current;
        const current = filterParamsRef.current;
        if (initial == null) return;
        if (getOnlyOneSourceType(streamsRef.current) != null) return;
        if (current.sourceType !== initial) {
            setFilterParams({ ...current, sourceType: initial });
        }
    }, []);

    useEffect(() => {
      const externalQuery = filterParams.query || '';
      if (externalQuery !== query) {
        setQuery(externalQuery);
      }
    }, [filterParams.query]);
    
    useEffect(() => {
      if (onGetPendingQuery) {
        onGetPendingQuery(() => query);
      }
    }, [query, onGetPendingQuery]);
    
    const open = useCallback(() => setIsPopoverOpen(true), []);
    const close = useCallback(() => setIsPopoverOpen(false), []);
    const togglePopover = useCallback(() => setIsPopoverOpen((prev) => !prev), []);

    // Close filter popover when content becomes disabled (e.g. chat mode turned on)
    useEffect(() => {
        if (contentDisabled) setIsPopoverOpen(false);
    }, [contentDisabled]);

    // Handle click outside to close popover
    useEffect(() => {
        if (!isPopoverOpen) return;

        const handleClickOutside = (event: MouseEvent) => {
            const target = event.target as HTMLElement;
            
            // Check if click is inside popover
            if (popoverRef.current && popoverRef.current.contains(target)) {
                return;
            }

            // Check if click is inside DatePicker calendar or CheckPicker dropdown
            const isDatePickerCalendar = target.closest('.rs-picker-menu, .rs-calendar, .rs-picker-popup');
            const isCheckPickerDropdown = target.closest('.rs-picker-menu, .rs-check-picker-menu');
            
            if (isDatePickerCalendar || isCheckPickerDropdown) {
                return;
            }

            // Check if click is on the filter button itself
            if (filterButtonRef.current && filterButtonRef.current.contains(target)) {
                return;
            }

            // If none of the above, close the popover
            close();
        };

        // Add delay to avoid closing immediately after opening
        const timeoutId = setTimeout(() => {
            document.addEventListener('mousedown', handleClickOutside);
        }, 100);

        return () => {
            clearTimeout(timeoutId);
            document.removeEventListener('mousedown', handleClickOutside);
        };
    }, [isPopoverOpen, close]);

    // Tag reset values lookup
    const tagResetValues: Record<string, any> = useMemo(() => ({
      startDate: { startDate: null },
      endDate: { endDate: null },
      videoSources: { videoSources: [] },
      similarity: { similarity: '' },
      topK: { topK: DEFAULT_TOP_K }
    }), []);
    
    const handleUpdateQuery = useCallback((value: string) => {
      setQuery(value);
      if (hasQueryError && value.trim()) {
        setHasQueryError(false);
      }
    }, [hasQueryError]);

    const handleSearch = useCallback(() => {
      if (!query.trim()) {
        setHasQueryError(true);
        return;
      }
      setHasQueryError(false);
      // Always use the search API path (agent or non-agent); do not send Search-submitted queries to the Chat sidebar.
      onUpdateSearchParams({ ...filterParams, query, sourceType });
    }, [query, filterParams, sourceType, onUpdateSearchParams]);

    const handleSourceTypeChange = useCallback((value: string | null) => {
      if (value && value !== sourceType) {
        try {
          sessionStorage.setItem(SOURCE_TYPE_STORAGE_KEY, value);
        } catch {
          // ignore
        }
        // Save current videoSources and restore saved ones for new type
        videoSourcesPerTypeRef.current[sourceType] = filterParams.videoSources || [];
        const savedVideoSources = videoSourcesPerTypeRef.current[value] || [];
        
        setSourceType(value);
        const newParams = { ...filterParams, sourceType: value, videoSources: savedVideoSources };
        setFilterParams(newParams);
        
        // Update filter tags based on savedVideoSources
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
      close();
    }, [filterParams, setFilterParams, addFilter, close]);

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
      () => (contentDisabled ? filterTags.filter((tag: FilterTag) => tag.key !== 'topK') : filterTags),
      [contentDisabled, filterTags]
    );

    return (
        <CustomProvider theme={theme}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
                <div style={{ width: 400, ...(hasQueryError ? { borderColor: '#f44336', boxShadow: '0 0 0 1px #f44336', borderRadius: 6 } : {}) }}>
                  <TextInput
                    data-testid="search-input"
                    value={query}
                    onValueChange={handleUpdateQuery}
                    placeholder="Search Files"
                    disabled={contentDisabled}
                    status={hasQueryError ? 'error' : undefined}
                    slotLeft={<SearchIcon />}
                    slotRight={
                      (query || isSearching) ? (
                        <CloseIcon
                          style={{
                            cursor: isSearching ? 'not-allowed' : 'pointer',
                            fontSize: 18,
                            color: theme === 'dark' ? '#ef4444' : '#dc2626',
                            transition: 'opacity 0.2s',
                            opacity: isSearching ? 0.4 : 0.7,
                          }}
                          onMouseEnter={isSearching ? undefined : (e: any) => (e.currentTarget.style.opacity = '1')}
                          onMouseLeave={isSearching ? undefined : (e: any) => (e.currentTarget.style.opacity = '0.7')}
                          onClick={isSearching ? undefined : () => handleUpdateQuery('')}
                        />
                      ) : contentDisabled ? undefined : (
                        <Whisper placement="bottom" speaker={<Tooltip>Ask a natural language query like "a person in green jacket carrying boxes"</Tooltip>}>
                          <InfoRoundIcon style={{ cursor: 'help', transition: 'opacity 0.2s' }} />
                        </Whisper>
                      )
                    }
                    onKeyDown={(e: React.KeyboardEvent) => { if (e.key === 'Enter') handleSearch(); }}
                  />
                </div>
                <KaizenButton
                  data-testid="search-button"
                  onClick={isSearching && onCancelSearch ? onCancelSearch : handleSearch}
                  disabled={isSearching && onCancelSearch ? false : contentDisabled}
                  kind={isSearching && onCancelSearch ? 'secondary' : 'primary'}
                >
                  {isSearching && onCancelSearch ? 'Cancel' : 'Search'}
                </KaizenButton>
                {isSearching && (
                  <span style={{ display: 'inline-flex', alignItems: 'center' }}>
                    <IconRefresh
                      style={{
                        width: 20,
                        height: 20,
                        flexShrink: 0,
                        color: theme === 'dark' ? '#60a5fa' : '#3b82f6',
                        animation: 'searchHeaderSpin 0.8s linear infinite',
                      }}
                    />
                  </span>
                )}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ whiteSpace: 'nowrap' }}>Source Type:</span>
                    {mounted && (
                      <Select
                          data-testid="search-source-type"
                          value={sourceType}
                          onValueChange={(val) => handleSourceTypeChange(val)}
                          disabled={contentDisabled}
                          items={SOURCE_TYPE_OPTIONS.map(opt => ({
                            value: opt.value,
                            children: opt.label,
                          }))}
                      />
                    )}
                </div>
                <div style={{ position: 'relative' }} ref={filterButtonRef}>
                    <KaizenButton data-testid="search-filter-button" onClick={togglePopover} disabled={contentDisabled}>Filter <FunnelIcon /></KaizenButton>
                    <FilterDialog
                      isOpen={isPopoverOpen}
                      isDark={theme === 'dark'}
                      disabled={contentDisabled}
                      handleConfirm={handleConfirm} 
                      close={close} 
                      streams={streams}
                      filterParams={filterParams}
                      setFilterParams={setFilterParams}
                      containerRef={popoverRef}
                      triggerRef={filterButtonRef}
                      sourceType={sourceType}
                    />
                </div>
                {visibleTags.length > 0 && (
                  <div data-testid="search-filter-tags" style={{ 
                    display: 'flex', 
                    flexWrap: 'wrap', 
                    gap: 5, 
                    alignItems: 'center',
                    pointerEvents: contentDisabled ? 'none' : 'auto'
                  }}>
                    {visibleTags.map((tag: FilterTag, index: number) => (
                      <KaizenTag
                        key={tag.key ?? index}
                        kind="outline"
                        color="gray"
                        readOnly={tag.key === 'topK' || contentDisabled}
                        style={{ opacity: contentDisabled ? 0.5 : 1 }}
                        onClick={!contentDisabled && tag.key !== 'topK' ? () => removeTag(tag) : undefined}
                      >
                        {tag.title}: <span style={{ color: theme === 'dark' ? '#84E1BC' : 'green' }}>{tag.value}</span>
                        {!contentDisabled && tag.key !== 'topK' && <IconX size={14} />}
                      </KaizenTag>
                    ))}
                    {visibleTags.length > 1 && (
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