// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { CustomProvider, Button } from 'rsuite';
import { Tag as KaizenTag } from '@nvidia/foundations-react-core';
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
  outerPadding?: React.CSSProperties['padding'];
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
  outerPadding = '8px 12px 12px',
}) => {
  const [isPopoverOpen, setIsPopoverOpen] = useState(false);

  const close = useCallback(() => setIsPopoverOpen(false), []);
  const togglePopover = useCallback(() => setIsPopoverOpen((prev) => !prev), []);

  useEffect(() => {
    if (contentDisabled) setIsPopoverOpen(false);
  }, [contentDisabled]);

  const tagResetValues: Record<string, any> = useMemo(() => ({
    videoSources: { videoSources: [] },
    similarity: { similarity: '' },
  }), []);

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
    const newParams = { ...filterParams, videoSources: [], similarity: 0 };
    removeFilterTag(null);
    setFilterParams(newParams);
  }, [filterParams, removeFilterTag, setFilterParams]);

  const visibleTags = filterTags;

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
