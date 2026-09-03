// SPDX-License-Identifier: MIT
import React from 'react';
import { act, render, screen } from '@testing-library/react';

import { SearchComponent } from '../../lib-src/SearchComponent';
import { useFilter } from '../../lib-src/hooks/useFilter';

jest.mock('../../lib-src/hooks/useFilter');

const mockUseFilter = useFilter as jest.MockedFunction<typeof useFilter>;

describe('SearchComponent sidebar events', () => {
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
      filterParams: { agentMode: true, similarity: 0.5, topK: 10 },
      setFilterParams: jest.fn(),
      addFilter: jest.fn(),
      removeFilterTag: jest.fn(),
      filterTags: [],
      refetch: jest.fn(),
    });
  });

  it('clears search results on sidebar messageSubmitted even when Search tab is not focused', () => {
    let subscriber;
    let chatAnswerHandler: ((answer: string) => boolean | void) | undefined;

    const registerSidebarChatEventSubscriber = jest.fn((handler) => {
      subscriber = handler;
      return jest.fn();
    });
    const registerChatAnswerHandler = jest.fn((handler) => {
      chatAnswerHandler = handler;
      return jest.fn();
    });

    render(
      <SearchComponent
        {...defaultProps}
        isActive={false}
        registerChatAnswerHandler={registerChatAnswerHandler}
        registerSidebarChatEventSubscriber={registerSidebarChatEventSubscriber}
      />,
    );

    expect(registerSidebarChatEventSubscriber).toHaveBeenCalledTimes(1);
    expect(subscriber).toBeDefined();

    act(() => {
      chatAnswerHandler?.(JSON.stringify({
        data: [{
          video_name: 'clip.mp4',
          description: 'a scene',
          start_time: '2024-01-01T00:00:00',
          end_time: '2024-01-01T00:05:00',
          sensor_id: 's1',
          similarity: 0.9,
          screenshot_url: '',
          object_ids: [],
        }],
      }));
    });

    expect(screen.getByText('clip.mp4')).toBeInTheDocument();

    act(() => {
      subscriber?.({ type: 'messageSubmitted' });
    });

    expect(screen.queryByText('clip.mp4')).not.toBeInTheDocument();
    expect(screen.getByText('Results will update here')).toBeInTheDocument();
  });

  it('does not clear results on sidebar answerComplete event', () => {
    let subscriber;
    let chatAnswerHandler: ((answer: string) => boolean | void) | undefined;

    const registerSidebarChatEventSubscriber = jest.fn((handler) => {
      subscriber = handler;
      return jest.fn();
    });
    const registerChatAnswerHandler = jest.fn((handler) => {
      chatAnswerHandler = handler;
      return jest.fn();
    });

    render(
      <SearchComponent
        {...defaultProps}
        registerChatAnswerHandler={registerChatAnswerHandler}
        registerSidebarChatEventSubscriber={registerSidebarChatEventSubscriber}
      />,
    );

    act(() => {
      chatAnswerHandler?.(JSON.stringify({
        data: [{
          video_name: 'keep.mp4',
          description: 'a scene',
          start_time: '2024-01-01T00:00:00',
          end_time: '2024-01-01T00:05:00',
          sensor_id: 's1',
          similarity: 0.9,
          screenshot_url: '',
          object_ids: [],
        }],
      }));
    });

    act(() => {
      subscriber?.({ type: 'answerComplete' });
    });

    expect(screen.getByText('keep.mp4')).toBeInTheDocument();
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

  it('pushes current Search filters into Chat context before paint', () => {
    const addChatQueryContext = jest.fn();
    render(
      <SearchComponent
        {...defaultProps}
        addChatQueryContext={addChatQueryContext}
      />,
    );

    expect(addChatQueryContext).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'vss-search-filters',
        data: expect.objectContaining({
          top_k: 10,
          min_cosine_similarity: 0.5,
        }),
      }),
    );
  });

  it('does not re-push filter context when the host rebuilds addChatQueryContext', () => {
    const addChatQueryContext = jest.fn();
    const onControlsReady = jest.fn();
    // Home builds a fresh addChatQueryContext on every render; that must not
    // make Search re-run its sync effect or rebuild its controls, or the two
    // components drive each other into an infinite render loop.
    const { rerender } = render(
      <SearchComponent
        {...defaultProps}
        renderControlsInLeftSidebar
        onControlsReady={onControlsReady}
        addChatQueryContext={(item) => addChatQueryContext(item)}
      />,
    );

    expect(addChatQueryContext).toHaveBeenCalledTimes(1);
    const controlsCallsAfterMount = onControlsReady.mock.calls.length;

    for (let i = 0; i < 3; i += 1) {
      rerender(
        <SearchComponent
          {...defaultProps}
          renderControlsInLeftSidebar
          onControlsReady={onControlsReady}
          addChatQueryContext={(item) => addChatQueryContext(item)}
        />,
      );
    }

    expect(addChatQueryContext).toHaveBeenCalledTimes(1);
    expect(onControlsReady).toHaveBeenCalledTimes(controlsCallsAfterMount);
  });

  it('applies Search-tab filters to Chat-derived result cards', () => {
    let chatAnswerHandler: ((answer: string) => boolean | void) | undefined;
    const registerChatAnswerHandler = jest.fn((handler) => {
      chatAnswerHandler = handler;
      return jest.fn();
    });

    render(
      <SearchComponent
        {...defaultProps}
        registerChatAnswerHandler={registerChatAnswerHandler}
      />,
    );

    act(() => {
      chatAnswerHandler?.(JSON.stringify({
        data: [
          {
            video_name: 'low.mp4',
            description: 'a scene',
            start_time: '2024-01-01T00:00:00',
            end_time: '2024-01-01T00:05:00',
            sensor_id: 's1',
            similarity: 0.2,
            screenshot_url: '',
            object_ids: [],
          },
          {
            video_name: 'high.mp4',
            description: 'a scene',
            start_time: '2024-01-01T00:00:00',
            end_time: '2024-01-01T00:05:00',
            sensor_id: 's1',
            similarity: 0.9,
            screenshot_url: '',
            object_ids: [],
          },
        ],
      }));
    });

    expect(screen.queryByText('low.mp4')).not.toBeInTheDocument();
    expect(screen.getByText('high.mp4')).toBeInTheDocument();
  });
});
