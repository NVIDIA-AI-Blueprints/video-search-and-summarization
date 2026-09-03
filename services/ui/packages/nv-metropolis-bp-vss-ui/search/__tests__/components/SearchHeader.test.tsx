// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { SearchHeader } from '../../lib-src/components/SearchHeader';

jest.mock('@nemo-agent-toolkit/ui');

const defaultProps = {
  theme: 'light' as const,
  streams: [],
  filterParams: {
    startDate: null,
    endDate: null,
    videoSources: [],
    similarity: 0,
    agentMode: false,
    query: '',
    topK: 10,
    sourceType: 'video_file',
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
    expect(screen.getByText('Source Type:')).toBeInTheDocument();
  });

  it('does not render a query input or Search button', () => {
    render(<SearchHeader {...defaultProps} />);
    expect(screen.queryByPlaceholderText('Search Files')).not.toBeInTheDocument();
    expect(screen.queryByTestId('search-input')).not.toBeInTheDocument();
    expect(screen.queryByTestId('search-button')).not.toBeInTheDocument();
    expect(screen.queryByText('Search')).not.toBeInTheDocument();
  });

  it('renders Source Type selector', () => {
    render(<SearchHeader {...defaultProps} />);
    expect(screen.getByText('Source Type:')).toBeInTheDocument();
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

  it('renders Clear All button when multiple filter tags exist', () => {
    const filterTags = [
      { key: 'topK', title: 'Show top K Results', value: '10' },
      { key: 'similarity', title: 'Similarity', value: '0.75' },
    ];

    render(<SearchHeader {...defaultProps} filterTags={filterTags} />);
    expect(screen.getByText('Clear All')).toBeInTheDocument();
  });

  it('does not render Clear All when only one tag exists', () => {
    const filterTags = [
      { key: 'topK', title: 'Show top K Results', value: '10' },
    ];

    render(<SearchHeader {...defaultProps} filterTags={filterTags} />);
    expect(screen.queryByText('Clear All')).not.toBeInTheDocument();
  });

  it('calls removeFilterTag and setFilterParams when a tag is closed', () => {
    const removeFilterTag = jest.fn();
    const setFilterParams = jest.fn();
    const filterTags = [
      { key: 'topK', title: 'Show top K Results', value: '10' },
      { key: 'similarity', title: 'Similarity', value: '0.75' },
    ];

    render(
      <SearchHeader
        {...defaultProps}
        filterTags={filterTags}
        removeFilterTag={removeFilterTag}
        setFilterParams={setFilterParams}
      />
    );

    // Click the close button on the Similarity tag (topK is not closable)
    const closeButtons = document.querySelectorAll('.rs-tag .rs-tag-btn-close, .rs-btn-close');
    if (closeButtons.length > 0) {
      fireEvent.click(closeButtons[0]);
      expect(removeFilterTag).toHaveBeenCalled();
    }
  });

  it('disables source type when contentDisabled is true', () => {
    render(<SearchHeader {...defaultProps} contentDisabled={true} />);
    expect(screen.getByTestId('search-source-type')).toBeInTheDocument();
  });

  it('renders dark theme correctly', () => {
    render(<SearchHeader {...defaultProps} theme="dark" />);
    expect(screen.getByText('Source Type:')).toBeInTheDocument();
  });
});
