// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { render, screen } from '@testing-library/react';
import { SearchSidebarControls } from '../../lib-src/components/SearchSidebarControls';
import { SIMILARITY_TAG_TOOLTIP } from '../../lib-src/utils/searchFilters';

const defaultProps = {
  theme: 'light' as const,
  isDark: false,
  streams: [],
  filterParams: {
    videoSources: [],
    similarity: 0,
    topK: 10,
  },
  setFilterParams: jest.fn(),
  addFilter: jest.fn(),
  removeFilterTag: jest.fn(),
  filterTags: [
    { key: 'topK', title: 'Show top K Results', value: '10' },
    { key: 'similarity', title: 'Similarity', value: '0.00' },
  ],
};

describe('SearchSidebarControls', () => {
  it('shows the top K tag alongside the other filter tags', () => {
    render(<SearchSidebarControls {...defaultProps} />);
    expect(screen.getByText(/Show top K Results/)).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument();
    expect(screen.getByText('0.00')).toBeInTheDocument();
  });

  it('does not let the Similarity tag be dismissed', () => {
    render(<SearchSidebarControls {...defaultProps} />);
    const similarity = screen.getByTestId('search-similarity-filter-tag');
    expect(similarity).toBeInTheDocument();
    expect(similarity.querySelector('svg')).toBeNull();
  });

  it('explains the Similarity tag in a tooltip', () => {
    render(<SearchSidebarControls {...defaultProps} />);
    expect(screen.getByTestId('search-similarity-filter-tag')).toHaveAttribute(
      'title',
      SIMILARITY_TAG_TOOLTIP,
    );
  });

  it('hides Clear All when filters are at defaults', () => {
    render(<SearchSidebarControls {...defaultProps} />);
    expect(screen.queryByText('Clear All')).not.toBeInTheDocument();
  });

  it('places Clear All below the filter tags when a non-default filter is set', () => {
    render(
      <SearchSidebarControls
        {...defaultProps}
        filterParams={{ ...defaultProps.filterParams, similarity: 0.75 }}
        filterTags={[
          { key: 'topK', title: 'Show top K Results', value: '10' },
          { key: 'similarity', title: 'Similarity', value: '0.75' },
        ]}
      />,
    );
    const tags = screen.getByTestId('search-filter-tags');
    const clearAll = screen.getByTestId('search-clear-all-filters');
    expect(tags.lastElementChild).toBe(clearAll);
  });
});
