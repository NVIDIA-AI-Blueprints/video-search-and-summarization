// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { render, screen } from '@testing-library/react';
import { SearchSidebarControls } from '../../lib-src/components/SearchSidebarControls';

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
    { key: 'similarity', title: 'Similarity', value: '0.75' },
  ],
};

describe('SearchSidebarControls', () => {
  it('shows the top K tag alongside the other filter tags', () => {
    render(<SearchSidebarControls {...defaultProps} />);
    expect(screen.getByText(/Show top K Results/)).toBeInTheDocument();
    expect(screen.getByText('10')).toBeInTheDocument();
    expect(screen.getByText('0.75')).toBeInTheDocument();
  });

  it('does not offer Clear All when top K is the only tag', () => {
    render(
      <SearchSidebarControls
        {...defaultProps}
        filterTags={[{ key: 'topK', title: 'Show top K Results', value: '10' }]}
      />,
    );
    expect(screen.getByText(/Show top K Results/)).toBeInTheDocument();
    expect(screen.queryByText('Clear All')).not.toBeInTheDocument();
  });
});
