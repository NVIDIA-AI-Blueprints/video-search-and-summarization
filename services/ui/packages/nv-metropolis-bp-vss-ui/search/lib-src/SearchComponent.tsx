// SPDX-License-Identifier: MIT
/**
 * Main Search Management Component
 * 
 * This is the primary component for the search management system, providing
 * a comprehensive interface for viewing, filtering, and managing security
 * and monitoring search with advanced time-based filtering capabilities.
 * 
 */
import React from 'react';
import dynamic from 'next/dynamic';
import { useVideoModal } from '@nemo-agent-toolkit/ui';
import { InfoRound as InfoRoundIcon } from '@rsuite/icons';
import { VideoModalTooltip } from 'common';

// Types
import { SearchComponentProps, SearchData } from './types';

// Hooks
import { useSearchByImage } from './hooks/useSearchByImage';
import { extractSearchResultsFromAgentResponse } from './utils/agentResponseParser';

// Components
import { SearchHeader } from './components/SearchHeader';
import { SearchSidebarControls } from './components/SearchSidebarControls';
import { VideoSearchList } from './components/VideoSearchList';
import { SearchVideoModal } from './components/SearchVideoModal';
import { SearchByImageOverlayInfo } from './components/SearchByImageOverlayInfo';
import { useFilter } from './hooks/useFilter';

const loadSearchByImageOverlay = () =>
  import('./components/SearchByImageOverlay').then((mod) => mod.SearchByImageOverlay);

const SearchByImageOverlayComponent = dynamic(loadSearchByImageOverlay, {
  ssr: false,
  loading: () => (
    <div className="flex items-center justify-center h-full min-h-[400px] bg-black text-white">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 border-2 border-white border-t-transparent rounded-full animate-spin" />
        <span className="text-sm">Preparing Search by Image overlay...</span>
      </div>
    </div>
  ),
});

export const SearchComponent: React.FC<SearchComponentProps> = ({
  theme = 'light',
  onThemeChange,
  isActive = true,
  searchData,
  renderControlsInLeftSidebar = false,
  onControlsReady,
  submitChatMessage,
  registerChatAnswerHandler,
  registerSidebarChatEventSubscriber,
  chatSidebarCollapsed = true,
  chatSidebarBusy = false,
  addChatQueryContext,
}) => {
  const isDark = theme === 'dark';
  const [agentSearchResults, setAgentSearchResults] = React.useState<SearchData[] | null>(null);

  React.useEffect(() => {
    loadSearchByImageOverlay()
      .then(() => undefined)
      .catch((error) => {
        console.error('[SearchComponent] Failed to preload SearchByImageOverlay:', error);
      });
  }, []);

  const vstApiUrl = searchData?.vstApiUrl;
  const mdxWebApiUrl = searchData?.mdxWebApiUrl;
  const mediaWithObjectsBbox = searchData?.mediaWithObjectsBbox ?? false;

  const { videoModal, openVideoModal, closeVideoModal } = useVideoModal(vstApiUrl);  
  const { streams, filterParams, setFilterParams, addFilter, removeFilterTag, filterTags, refetch: refetchStreams } = useFilter({vstApiUrl});

  // Map streamId (UUID) -> sensor name for /frames API lookup
  const sensorIdToNameMap = React.useMemo(() => {
    const map = new Map<string, string>();
    streams.forEach((stream) => map.set(stream.sensorId, stream.name));
    return map;
  }, [streams]);

  // Track which SearchData item is currently playing so Search by Image knows sensorId + start_time.
  const [activeVideoData, setActiveVideoData] = React.useState<SearchData | null>(null);

  // Search by Image hook
  const {
    searchByImageActive,
    searchByImageLoading,
    searchByImageError,
    searchByImageFrameData,
    startSearchByImage,
    cancelSearchByImage,
  } = useSearchByImage({ vstApiUrl, mdxWebApiUrl });
  const [searchByImageSelectedObjectId, setSearchByImageSelectedObjectId] = React.useState<string | null>(null);

  React.useEffect(() => {
    setSearchByImageSelectedObjectId(null);
  }, [searchByImageFrameData, searchByImageActive]);

  const playRequestIdRef = React.useRef(0);

  const handlePlayVideo = React.useCallback(
    async (data: SearchData, showBbox: boolean): Promise<boolean> => {
      const requestId = ++playRequestIdRef.current;
      setActiveVideoData(data);
      const opened = await openVideoModal(data, showBbox);
      // Playing another card aborts this request, which also resolves false.
      // Don't let the superseded card report that as a playback failure.
      if (!opened && requestId !== playRequestIdRef.current) return true;
      return opened;
    },
    [openVideoModal]
  );

  const handleCloseVideoModal = React.useCallback(() => {
    closeVideoModal();
    cancelSearchByImage();
    setActiveVideoData(null);
  }, [closeVideoModal, cancelSearchByImage]);

  const handleSearchByImageRequest = React.useCallback(
    (pauseOffsetSeconds: number) => {
      if (!activeVideoData) return;
      const sensorName = sensorIdToNameMap.get(activeVideoData.sensor_id) || activeVideoData.sensor_id;
      startSearchByImage(activeVideoData.sensor_id, sensorName, activeVideoData.start_time, pauseOffsetSeconds, videoModal.videoUrl);
    },
    [activeVideoData, startSearchByImage, videoModal.videoUrl, sensorIdToNameMap]
  );

  const handleSearchByImageConfirm = React.useCallback((objectId: string) => {
    if (!submitChatMessage) return;
    const prompt = `Find similar objects matching object_id=${objectId}`;
    submitChatMessage(prompt);
    cancelSearchByImage();
    closeVideoModal();
    setActiveVideoData(null);
  }, [submitChatMessage, cancelSearchByImage, closeVideoModal]);

  const refetchStreamsRef = React.useRef(refetchStreams);

  React.useEffect(() => {
    refetchStreamsRef.current = refetchStreams;
  }, [refetchStreams]);

  React.useEffect(() => {
    if (isActive) {
      refetchStreamsRef.current();
    }
  }, [isActive]);

  // Stable forwarder + ref so Home's register callback stays identity-stable while we always invoke the latest parser/setState.
  const deliverAgentAnswerRef = React.useRef<(answer: string) => boolean>(() => false);
  deliverAgentAnswerRef.current = (answer: string) => {
    const results = extractSearchResultsFromAgentResponse(answer);
    if (results !== null) {
      setAgentSearchResults(results);
      return true;
    }
    return false;
  };
  const forwardAgentAnswer = React.useCallback((answer: string) => {
    return deliverAgentAnswerRef.current(answer);
  }, []);

  React.useEffect(() => {
    if (!registerChatAnswerHandler) return;
    return registerChatAnswerHandler(forwardAgentAnswer);
  }, [registerChatAnswerHandler, forwardAgentAnswer]);

  // Clear main results on any Chat sidebar send (bridge emits messageSubmitted to Search even when another tab is focused).
  React.useEffect(() => {
    if (!registerSidebarChatEventSubscriber) return;
    const unsubscribe = registerSidebarChatEventSubscriber((event) => {
      if (event.type === 'messageSubmitted') {
        setAgentSearchResults(null);
      }
    });
    return typeof unsubscribe === 'function' ? unsubscribe : undefined;
  }, [registerSidebarChatEventSubscriber]);

  const contentDisabled = !chatSidebarCollapsed || chatSidebarBusy;

  const controlsComponent = React.useMemo(
    () => (
      <SearchSidebarControls
        isDark={isDark}
        theme={isDark ? 'dark' : 'light'}
        compactLayout={renderControlsInLeftSidebar}
        outerPadding={renderControlsInLeftSidebar ? '8px 8px 12px' : 0}
        streams={streams}
        filterParams={filterParams}
        setFilterParams={setFilterParams}
        addFilter={addFilter}
        removeFilterTag={removeFilterTag}
        filterTags={filterTags}
        contentDisabled={contentDisabled}
      />
    ),
    [
      isDark,
      renderControlsInLeftSidebar,
      streams,
      filterParams,
      setFilterParams,
      addFilter,
      removeFilterTag,
      filterTags,
      contentDisabled,
    ]
  );

  React.useEffect(() => {
    if (onControlsReady && renderControlsInLeftSidebar) {
      onControlsReady({
        isDark,
        onRefresh: refetchStreams,
        controlsComponent,
      });
    }
  }, [onControlsReady, renderControlsInLeftSidebar, isDark, refetchStreams, controlsComponent]);

  const searchByImageFooterElement = React.useMemo(() => {
    if (!searchByImageActive) return undefined;

    return (
      <SearchByImageOverlayInfo
        frameData={searchByImageFrameData}
        selectedObjectId={searchByImageSelectedObjectId}
        onConfirm={handleSearchByImageConfirm}
        onCancel={cancelSearchByImage}
        isDark={isDark}
      />
    );
  }, [searchByImageActive, searchByImageFrameData, searchByImageSelectedObjectId, handleSearchByImageConfirm, cancelSearchByImage, isDark]);

  // Build Search by Image overlay element when Search by Image is active
  const searchByImageOverlayElement = React.useMemo(() => {
    if (!searchByImageActive) return undefined;

    let content: React.ReactNode;

    if (searchByImageLoading) {
      content = (
        <div
          data-testid="search-by-image-loading"
          className="flex h-full min-h-[400px] items-center justify-center bg-black text-white"
        >
          <div className="flex flex-col items-center gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-white border-t-transparent" />
            <span className="text-sm">Loading frame data for Search by Image...</span>
          </div>
        </div>
      );
    } else if (searchByImageError) {
      content = (
        <div
          data-testid="search-by-image-error"
          className="flex h-full min-h-[400px] items-center justify-center bg-black text-red-400"
        >
          <div className="flex max-w-md flex-col items-center gap-3 text-center">
            <span className="text-sm">{searchByImageError}</span>
          </div>
        </div>
      );
    } else if (searchByImageFrameData) {
      content = (
        <SearchByImageOverlayComponent
          frameData={searchByImageFrameData}
          selectedObjectId={searchByImageSelectedObjectId}
          onSelectObject={setSearchByImageSelectedObjectId}
        />
      );
    }

    if (!content) return undefined;

    return <div className="h-full min-h-0">{content}</div>;
  }, [searchByImageActive, searchByImageLoading, searchByImageError, searchByImageFrameData, searchByImageSelectedObjectId]);

  const modalTitle = searchByImageActive ? (
    <span className="inline-flex items-baseline gap-2">
      <span className="inline-flex items-center gap-2">
        <span>{videoModal.title}</span>
      </span>
      <span className="max-w-[360px] truncate text-xs font-normal text-gray-600 dark:text-gray-400" style={{display: 'flex', alignItems: 'center', gap: '3px', marginBottom: '2px'}}>
        ( Search by Image Mode 
        <VideoModalTooltip
          content="Select a bounding box to perform Agentic Search for similar objects across cameras. Press Cancel below to exit this mode"
          placement="bottomStart"
        >
          <InfoRoundIcon style={{ cursor: 'help', opacity: 0.75, fontSize: 11, marginBottom: '3px'}} />
        </VideoModalTooltip>)
      </span>
    </span>
  ) : (
    videoModal.title
  );
  
  return (
    <div 
      data-testid="search-component"
      className={`flex min-h-0 min-w-0 max-w-full flex-col h-full max-h-full ${isDark ? 'bg-black text-gray-100' : 'bg-gray-50 text-gray-900'}`}
    >
      {/* Source type + filters live in the left sidebar when the parent app renders them
          there; the in-tab header is the standalone-only alternative. Rendering
          both would mount two copies of the same controls, each with its own
          sourceType state. */}
      {!renderControlsInLeftSidebar && (
        <div className={`flex-shrink-0 px-6 py-4 border-b ${isDark ? 'bg-black border-gray-700' : 'bg-white border-gray-200'}`}>
          <SearchHeader
            theme={isDark ? 'dark' : 'light'}
            streams={streams}
            filterParams={filterParams}
            setFilterParams={setFilterParams}
            addFilter={addFilter}
            removeFilterTag={removeFilterTag}
            filterTags={filterTags}
            contentDisabled={contentDisabled}
          />
        </div>
      )}
      <div className="flex-1 overflow-auto">
        <VideoSearchList
          data={agentSearchResults ?? []}
          loading={false}
          error={null}
          isDark={isDark}
          onRefresh={refetchStreams}
          onPlayVideo={handlePlayVideo}
          showObjectsBbox={mediaWithObjectsBbox}
          onAddContext={addChatQueryContext}
        />
      </div>
      <SearchVideoModal
        isOpen={videoModal.isOpen}
        videoUrl={videoModal.videoUrl}
        title={modalTitle}
        onClose={handleCloseVideoModal}
        searchByImageEnabled={mediaWithObjectsBbox}
        onSearchByImageRequest={handleSearchByImageRequest}
        searchByImageFooter={searchByImageFooterElement}
        searchByImageOverlay={searchByImageOverlayElement}
      />
    </div>
  );
};

// Re-export types for convenience
export type { SearchData, SearchComponentProps } from './types';

