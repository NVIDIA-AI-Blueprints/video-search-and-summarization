// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { Tag as KaizenTag } from '@nvidia/foundations-react-core';
import { IconX } from '@tabler/icons-react';
import { Whisper, Tooltip } from 'rsuite';
import { SIMILARITY_TAG_TOOLTIP } from '../utils/searchFilters';
import type { FilterTag } from '../types';

const PERSISTENT_FILTER_TAG_KEYS = new Set(['similarity', 'topK']);

export function isPersistentFilterTag(key: string): boolean {
  return PERSISTENT_FILTER_TAG_KEYS.has(key);
}

interface FilterTagChipProps {
  tag: FilterTag;
  theme: 'light' | 'dark';
  contentDisabled?: boolean;
  onRemove: (tag: FilterTag) => void;
}

export const FilterTagChip: React.FC<FilterTagChipProps> = ({
  tag,
  theme,
  contentDisabled = false,
  onRemove,
}) => {
  const persistent = isPersistentFilterTag(tag.key);
  const chip = (
    <KaizenTag
      kind="outline"
      color="gray"
      readOnly={persistent || contentDisabled}
      style={{ opacity: contentDisabled ? 0.5 : 1 }}
      onClick={!contentDisabled && !persistent ? () => onRemove(tag) : undefined}
    >
      {tag.title}: <span style={{ color: theme === 'dark' ? '#84E1BC' : 'green' }}>{tag.value}</span>
      {!contentDisabled && !persistent && <IconX size={14} />}
    </KaizenTag>
  );

  if (tag.key !== 'similarity') return chip;

  return (
    <Whisper
      trigger="hover"
      placement="top"
      speaker={<Tooltip>{SIMILARITY_TAG_TOOLTIP}</Tooltip>}
    >
      <span
        data-testid="search-similarity-filter-tag"
        aria-label={SIMILARITY_TAG_TOOLTIP}
        title={SIMILARITY_TAG_TOOLTIP}
        style={{ display: 'inline-flex' }}
      >
        {chip}
      </span>
    </Whisper>
  );
};
