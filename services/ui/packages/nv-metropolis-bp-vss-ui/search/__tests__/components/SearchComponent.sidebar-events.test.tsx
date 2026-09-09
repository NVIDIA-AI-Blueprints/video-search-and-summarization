// SPDX-License-Identifier: MIT
import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useVideoModal } from 'common';

import { SearchComponent } from '../../lib-src/SearchComponent';
import { useFilter } from '../../lib-src/hooks/useFilter';
import { useSearchByImage } from '../../lib-src/hooks/useSearchByImage';

jest.mock('../../lib-src/hooks/useFilter');
jest.mock('../../lib-src/hooks/useSearchByImage');
jest.mock('common', () => ({
  ...jest.requireActual('common'),
  useVideoModal: jest.fn(),
}));
jest.mock('next/dynamic', () => () => {
  const DynamicOverlay = ({ onSelectObject }: { onSelectObject: (id: string) => void }) =>
    React.createElement('button', {
      type: 'button',
      'data-testid': 'select-search-by-image-object',
      onClick: () => onSelectObject('42'),
    });
  return DynamicOverlay;
});

const mockUseFilter = useFilter as jest.MockedFunction<typeof useFilter>;
const mockUseSearchByImage = useSearchByImage as jest.MockedFunction<typeof useSearchByImage>;
const mockUseVideoModal = useVideoModal as jest.MockedFunction<typeof useVideoModal>;

const inactiveSearchByImage = {
  searchByImageActive: false,
  searchByImageLoading: false,
  searchByImageError: null,
  searchByImageFrameData: null,
  startSearchByImage: jest.fn(),
  cancelSearchByImage: jest.fn(),
};

const closedVideoModal = {
  videoModal: { isOpen: false, videoUrl: '', title: '' },
  openVideoModal: jest.fn(),
  closeVideoModal: jest.fn(),
};

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
    mockUseSearchByImage.mockReturnValue({ ...inactiveSearchByImage });
    mockUseVideoModal.mockReturnValue({ ...closedVideoModal });
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

  it('does not push Search filters into Chat context', () => {
    const addChatQueryContext = jest.fn();
    render(
      <SearchComponent
        {...defaultProps}
        addChatQueryContext={addChatQueryContext}
      />,
    );

    expect(addChatQueryContext).not.toHaveBeenCalled();
  });

  it('does not re-register controls when the host rebuilds addChatQueryContext', () => {
    const addChatQueryContext = jest.fn();
    const onControlsReady = jest.fn();
    // Home builds a fresh addChatQueryContext on every render; that must not
    // rebuild Search controls or the two components drive each other into an
    // infinite render loop.
    const { rerender } = render(
      <SearchComponent
        {...defaultProps}
        renderControlsInLeftSidebar
        onControlsReady={onControlsReady}
        addChatQueryContext={(item) => addChatQueryContext(item)}
      />,
    );

    expect(addChatQueryContext).not.toHaveBeenCalled();
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

    expect(addChatQueryContext).not.toHaveBeenCalled();
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

  it('submits an unprefixed Search-by-Image object_id prompt', async () => {
    const submitChatMessage = jest.fn();
    const cancelSearchByImage = jest.fn();
    const closeVideoModal = jest.fn();

    mockUseVideoModal.mockReturnValue({
      videoModal: { isOpen: true, videoUrl: 'http://vst.test/clip.mp4', title: 'clip' },
      openVideoModal: jest.fn(),
      closeVideoModal,
    });
    mockUseSearchByImage.mockReturnValue({
      searchByImageActive: true,
      searchByImageLoading: false,
      searchByImageError: null,
      searchByImageFrameData: {
        frameImage: {} as HTMLImageElement,
        objects: [{ id: '42', bbox: { leftX: 0, topY: 0, rightX: 1, bottomY: 1 }, type: 'Person' }],
        sensorId: 's1',
        sensorName: 'cam',
        timestamp: '2024-01-01T00:00:00',
      },
      startSearchByImage: jest.fn(),
      cancelSearchByImage,
    });

    render(
      <SearchComponent
        {...defaultProps}
        submitChatMessage={submitChatMessage}
      />,
    );

    await waitFor(() => {
      expect(screen.getByTestId('select-search-by-image-object')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId('select-search-by-image-object'));
    fireEvent.click(screen.getByTestId('search-by-image-search-button'));

    expect(submitChatMessage).toHaveBeenCalledTimes(1);
    expect(submitChatMessage).toHaveBeenCalledWith('Find similar objects matching object_id=42');
    expect(submitChatMessage.mock.calls[0][0]).not.toContain('[Context:');
    expect(cancelSearchByImage).toHaveBeenCalledTimes(1);
    expect(closeVideoModal).toHaveBeenCalledTimes(1);
  });
});
