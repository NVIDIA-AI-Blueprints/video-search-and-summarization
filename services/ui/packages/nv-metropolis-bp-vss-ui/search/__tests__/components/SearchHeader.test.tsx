// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { SearchHeader } from '../../lib-src/components/SearchHeader';

const defaultProps = {
  theme: 'light' as const,
  streams: [],
  filterParams: {
    videoSources: [],
    similarity: 0,
    agentMode: false,
    query: '',
    topK: 10,
  },
  setFilterParams: jest.fn(),
  addFilter: jest.fn(),
  removeFilterTag: jest.fn(),
  filterTags: [],
};

describe('SearchHeader', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders without crashing', () => {
    render(<SearchHeader {...defaultProps} />);
    expect(screen.getByText('Filter')).toBeInTheDocument();
  });

  it('does not render a query input or Search button', () => {
    render(<SearchHeader {...defaultProps} />);
    expect(screen.queryByPlaceholderText('Search Files')).not.toBeInTheDocument();
    expect(screen.queryByTestId('search-input')).not.toBeInTheDocument();
    expect(screen.queryByTestId('search-button')).not.toBeInTheDocument();
    expect(screen.queryByText('Search')).not.toBeInTheDocument();
  });

  it('renders Filter button', () => {
    render(<SearchHeader {...defaultProps} />);
    expect(screen.getByText('Filter')).toBeInTheDocument();
  });

  it('renders filter tags when provided', () => {
    const filterTags = [
      { key: 'topK', title: 'Show top K Results', value: '10' },
      { key: 'similarity', title: 'Similarity', value: '0.75' },
    ];

    render(<SearchHeader {...defaultProps} filterTags={filterTags} />);

    expect(screen.getByText(/Show top K Results/)).toBeInTheDocument();
    expect(screen.getByText('0.75')).toBeInTheDocument();
  });

  it('renders Clear All when filters are not at defaults', () => {
    const filterTags = [
      { key: 'topK', title: 'Show top K Results', value: '10' },
      { key: 'similarity', title: 'Similarity', value: '0.75' },
    ];

    render(
      <SearchHeader
        {...defaultProps}
        filterParams={{ ...defaultProps.filterParams, similarity: 0.75 }}
        filterTags={filterTags}
      />,
    );
    expect(screen.getByText('Clear All')).toBeInTheDocument();
  });

  it('does not render Clear All when filters are at defaults', () => {
    const filterTags = [
      { key: 'topK', title: 'Show top K Results', value: '10' },
      { key: 'similarity', title: 'Similarity', value: '0.00' },
    ];

    render(<SearchHeader {...defaultProps} filterTags={filterTags} />);
    expect(screen.queryByText('Clear All')).not.toBeInTheDocument();
  });

  it('does not dismiss the Similarity tag', () => {
    const removeFilterTag = jest.fn();
    const filterTags = [
      { key: 'topK', title: 'Show top K Results', value: '10' },
      { key: 'similarity', title: 'Similarity', value: '0.00' },
    ];

    render(
      <SearchHeader
        {...defaultProps}
        filterTags={filterTags}
        removeFilterTag={removeFilterTag}
      />,
    );

    expect(screen.getByTestId('search-similarity-filter-tag').querySelector('svg')).toBeNull();
    fireEvent.click(screen.getByTestId('search-similarity-filter-tag'));
    expect(removeFilterTag).not.toHaveBeenCalled();
  });

  it('disables the filter button when contentDisabled is true', () => {
    render(<SearchHeader {...defaultProps} contentDisabled={true} />);
    expect(screen.getByTestId('search-filter-button')).toBeDisabled();
  });

  it('keeps the top K tag visible when content is disabled', () => {
    const filterTags = [
      { key: 'topK', title: 'Show top K Results', value: '10' },
      { key: 'similarity', title: 'Similarity', value: '0.75' },
    ];
    render(<SearchHeader {...defaultProps} contentDisabled filterTags={filterTags} />);
    expect(screen.getByText(/Show top K Results/)).toBeInTheDocument();
    expect(screen.getByText('0.75')).toBeInTheDocument();
  });

  it('renders dark theme correctly', () => {
    render(<SearchHeader {...defaultProps} theme="dark" />);
    expect(screen.getByText('Filter')).toBeInTheDocument();
  });
});
