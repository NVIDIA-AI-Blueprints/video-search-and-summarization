// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React, { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import { CustomProvider, Button } from 'rsuite';
import { Button as KaizenButton } from '@nvidia/foundations-react-core';
import { Funnel as FunnelIcon } from '@rsuite/icons';
import { FilterDialog } from './FilterPopover';
import { FilterTagChip } from './FilterTagChip';
import { StreamInfo, FilterTag } from '../types';
import { DEFAULT_TOP_K } from '../hooks/useFilter';
import { isDefaultSearchFilters } from '../utils/searchFilters';

interface SearchHeaderProps {
    theme: 'light' | 'dark';
    streams: StreamInfo[];
    filterParams: any;
    setFilterParams: (params: any) => void;
    addFilter: (params?: any) => void;
    removeFilterTag: (tag: FilterTag | null) => void;
    filterTags: FilterTag[];
    /** When true, disables filters and tags (e.g. when Chat sidebar is open or query is running). */
    contentDisabled?: boolean;
  }

export const SearchHeader: React.FC<SearchHeaderProps> = ({ theme, streams, filterParams, setFilterParams, addFilter, removeFilterTag, filterTags, contentDisabled = false }) => {
    const [isPopoverOpen, setIsPopoverOpen] = useState(false);
    const popoverRef = useRef<HTMLDivElement>(null);
    const filterButtonRef = useRef<HTMLDivElement>(null);

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

            // Check if click is inside a picker dropdown
            const isPickerDropdown = target.closest('.rs-picker-menu, .rs-picker-popup, .rs-check-picker-menu');

            if (isPickerDropdown) {
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
      videoSources: { videoSources: [] },
    }), []);

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
      const newParams = {
        ...filterParams,
        videoSources: [],
        similarity: 0,
        topK: DEFAULT_TOP_K,
      };
      setFilterParams(newParams);
      addFilter(newParams);
    }, [filterParams, setFilterParams, addFilter]);

    const visibleTags = filterTags;

    return (
        <CustomProvider theme={theme}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, alignItems: 'center' }}>
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
                      <FilterTagChip
                        key={tag.key ?? index}
                        tag={tag}
                        theme={theme}
                        contentDisabled={contentDisabled}
                        onRemove={removeTag}
                      />
                    ))}
                    {!isDefaultSearchFilters(filterParams) && (
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
