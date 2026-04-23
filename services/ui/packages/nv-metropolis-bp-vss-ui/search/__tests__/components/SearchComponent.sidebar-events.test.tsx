// SPDX-License-Identifier: MIT
import React from 'react';
import { act, render } from '@testing-library/react';

import { SearchComponent } from '../../lib-src/SearchComponent';
import { useFilter } from '../../lib-src/hooks/useFilter';
import { useSearch } from '../../lib-src/hooks/useSearch';

jest.mock('../../lib-src/hooks/useSearch');
jest.mock('../../lib-src/hooks/useFilter');

const mockUseSearch = useSearch as jest.MockedFunction<typeof useSearch>;
const mockUseFilter = useFilter as jest.MockedFunction<typeof useFilter>;

describe('SearchComponent sidebar events', () => {
  const clearSearchResults = jest.fn();

  const defaultProps = {
    theme: 'light',
    isActive: true,
    searchData: {
      systemStatus: 'ok',
      agentApiUrl: 'http://agent-api.test',
      vstApiUrl: 'http://vst-api.test',
    },
  };

  beforeEach(() => {
    jest.clearAllMocks();

    mockUseFilter.mockReturnValue({
      streams: [],
      filterParams: { agentMode: true },
      setFilterParams: jest.fn(),
      addFilter: jest.fn(),
      removeFilterTag: jest.fn(),
      filterTags: [],
      refetch: jest.fn(),
    });

    mockUseSearch.mockReturnValue({
      searchResults: [],
      loading: false,
      error: null,
      refetch: jest.fn(),
      onUpdateSearchParams: jest.fn(),
      cancelSearch: jest.fn(),
      clearSearchResults,
    });
  });

  it('clears search results on sidebar messageSubmitted even when Search tab is not focused', () => {
    let subscriber;

    const registerSidebarChatEventSubscriber = jest.fn((handler) => {
      subscriber = handler;
      return jest.fn();
    });

    render(
      <SearchComponent
        {...defaultProps}
        isActive={false}
        registerSidebarChatEventSubscriber={registerSidebarChatEventSubscriber}
      />,
    );

    expect(registerSidebarChatEventSubscriber).toHaveBeenCalledTimes(1);
    expect(subscriber).toBeDefined();

    act(() => {
      subscriber?.({ type: 'messageSubmitted' });
    });

    expect(clearSearchResults).toHaveBeenCalledTimes(1);
  });

  it('does not clear results on sidebar answerComplete event', () => {
    let subscriber;

    const registerSidebarChatEventSubscriber = jest.fn((handler) => {
      subscriber = handler;
      return jest.fn();
    });

    render(
      <SearchComponent
        {...defaultProps}
        registerSidebarChatEventSubscriber={registerSidebarChatEventSubscriber}
      />,
    );

    act(() => {
      subscriber?.({ type: 'answerComplete' });
    });

    expect(clearSearchResults).not.toHaveBeenCalled();
  });

  it('unsubscribes sidebar event handler on unmount', () => {
    const unsubscribe = jest.fn();
    const registerSidebarChatEventSubscriber = jest.fn(() => unsubscribe);

    const { unmount } = render(
      <SearchComponent
        {...defaultProps}
        registerSidebarChatEventSubscriber={registerSidebarChatEventSubscriber}
      />,
    );

    unmount();

    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });
});
