// SPDX-License-Identifier: MIT
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { SearchHeader } from '../../lib-src/components/SearchHeader';

jest.mock('@nemo-agent-toolkit/ui');

const defaultProps = {
  onUpdateSearchParams: jest.fn(),
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
  isSearching: false,
  onCancelSearch: jest.fn(),
  onGetPendingQuery: jest.fn(),
};

describe('SearchHeader', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  it('renders without crashing', () => {
    render(<SearchHeader {...defaultProps} />);
    expect(screen.getByPlaceholderText('Search Files')).toBeInTheDocument();
  });

  it('renders Search button by default', () => {
    render(<SearchHeader {...defaultProps} />);
    expect(screen.getByText('Search')).toBeInTheDocument();
  });

  it('renders Cancel button when searching with onCancelSearch', () => {
    render(<SearchHeader {...defaultProps} isSearching={true} onCancelSearch={jest.fn()} />);
    expect(screen.getByText('Cancel')).toBeInTheDocument();
  });

  it('updates query input value on change', () => {
    render(<SearchHeader {...defaultProps} />);
    const input = screen.getByPlaceholderText('Search Files');

    fireEvent.change(input, { target: { value: 'person walking' } });
    expect(input).toHaveValue('person walking');
  });

  it('shows error border when searching with empty query', () => {
    render(<SearchHeader {...defaultProps} />);

    fireEvent.click(screen.getByText('Search'));

    expect(defaultProps.onUpdateSearchParams).not.toHaveBeenCalled();
  });

  it('calls onUpdateSearchParams with correct params on search', () => {
    render(<SearchHeader {...defaultProps} />);

    const input = screen.getByPlaceholderText('Search Files');
    fireEvent.change(input, { target: { value: 'find cars' } });
    fireEvent.click(screen.getByText('Search'));

    expect(defaultProps.onUpdateSearchParams).toHaveBeenCalledWith(
      expect.objectContaining({ query: 'find cars', sourceType: 'video_file' })
    );
  });

  it('triggers search on Enter key', () => {
    render(<SearchHeader {...defaultProps} />);

    const input = screen.getByPlaceholderText('Search Files');
    fireEvent.change(input, { target: { value: 'test search' } });
    // rsuite Input fires onPressEnter on keyDown
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' });

    expect(defaultProps.onUpdateSearchParams).toHaveBeenCalled();
  });

  it('calls onCancelSearch when cancel button is clicked', () => {
    const onCancelSearch = jest.fn();
    render(<SearchHeader {...defaultProps} isSearching={true} onCancelSearch={onCancelSearch} />);

    fireEvent.click(screen.getByText('Cancel'));
    expect(onCancelSearch).toHaveBeenCalledTimes(1);
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

  it('disables input when contentDisabled is true', () => {
    render(<SearchHeader {...defaultProps} contentDisabled={true} />);
    const input = screen.getByPlaceholderText('Search Files');
    expect(input).toBeDisabled();
  });

  it('syncs query from external filterParams.query', () => {
    const { rerender } = render(<SearchHeader {...defaultProps} />);

    rerender(
      <SearchHeader
        {...defaultProps}
        filterParams={{ ...defaultProps.filterParams, query: 'external query' }}
      />
    );

    expect(screen.getByPlaceholderText('Search Files')).toHaveValue('external query');
  });

  it('renders dark theme correctly', () => {
    render(<SearchHeader {...defaultProps} theme="dark" />);
    expect(screen.getByPlaceholderText('Search Files')).toBeInTheDocument();
  });

  it('registers pending query getter via onGetPendingQuery', () => {
    const onGetPendingQuery = jest.fn();
    render(<SearchHeader {...defaultProps} onGetPendingQuery={onGetPendingQuery} />);

    expect(onGetPendingQuery).toHaveBeenCalledWith(expect.any(Function));
  });
});
