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
import { VideoModal } from '@nemo-agent-toolkit/ui';

// Types
import { SearchComponentProps, SearchData } from './types';

// Hooks
import { useSearch } from './hooks/useSearch';
import { extractSearchResultsFromAgentResponse } from './utils/agentResponseParser';
import { useVideoModal } from './hooks/useVideoModal';

// Components
import { SearchHeader } from './components/SearchHeader';
import { SearchSidebarControls } from './components/SearchSidebarControls';
import { VideoSearchList } from './components/VideoSearchList';
import { useFilter } from './hooks/useFilter';

export const SearchComponent: React.FC<SearchComponentProps> = ({
  theme = 'light',
  onThemeChange,
  isActive = true,
  searchData,
  renderControlsInLeftSidebar = false,
  onControlsReady,
  submitChatMessage,
  registerChatAnswerHandler,
  chatSidebarCollapsed = true,
  chatSidebarBusy = false,
}) => {
  const isDark = theme === 'dark';
  const [agentSearchResults, setAgentSearchResults] = React.useState<SearchData[] | null>(null);

  const agentApiUrl = searchData?.agentApiUrl;
  const vstApiUrl = searchData?.vstApiUrl;
  const mediaWithObjectsBbox = searchData?.mediaWithObjectsBbox ?? false;

  const { videoModal, openVideoModal, closeVideoModal } = useVideoModal(vstApiUrl);  
  const { streams, filterParams, setFilterParams, addFilter, removeFilterTag, filterTags, refetch: refetchStreams } = useFilter({vstApiUrl});
  const { searchResults, loading, error, refetch, onUpdateSearchParams, cancelSearch, clearSearchResults } = useSearch({
    agentApiUrl, 
    params: filterParams
  });

  const refetchStreamsRef = React.useRef(refetchStreams);
  const getPendingQueryRef = React.useRef<() => string>(() => '');

  React.useEffect(() => {
    refetchStreamsRef.current = refetchStreams;
  }, [refetchStreams]);

  const handleGetPendingQuery = React.useCallback((getPendingFn: () => string) => {
    getPendingQueryRef.current = getPendingFn;
  }, []);

  React.useEffect(() => {
    if (isActive) {
      refetchStreamsRef.current();
    }
  }, [isActive]);

  // When agent mode is off, show normal search results (clear agent-driven results).
  React.useEffect(() => {
    if (!filterParams.agentMode) {
      setAgentSearchResults(null);
    }
  }, [filterParams.agentMode]);

  // Clear video results when Search button is pressed (loading started).
  React.useEffect(() => {
    if (loading) {
      setAgentSearchResults(null);
    }
  }, [loading]);

  // Clear video results as soon as a new message is submitted in the Chat sidebar (transition to busy).
  const prevChatSidebarBusyRef = React.useRef(chatSidebarBusy);
  React.useEffect(() => {
    const becameBusy = chatSidebarBusy && !prevChatSidebarBusyRef.current;
    prevChatSidebarBusyRef.current = chatSidebarBusy;
    if (becameBusy) {
      setAgentSearchResults(null);
      clearSearchResults?.();
    }
  }, [chatSidebarBusy, clearSearchResults]);

  // Register handler to extract Search API–shaped JSON from agent answers and update main content.
  React.useEffect(() => {
    if (!registerChatAnswerHandler) return;
    return registerChatAnswerHandler((answer: string) => {
      const results = extractSearchResultsFromAgentResponse(answer);
      if (results !== null) {
        setAgentSearchResults(results);
      }
    });
  }, [registerChatAnswerHandler]);

  const controlsComponent = React.useMemo(
    () => (
      <SearchSidebarControls
        isDark={isDark}
        onRefresh={refetch}
      />
    ),
    [
      isDark,
      refetch,
    ]
  );

  React.useEffect(() => {
    if (onControlsReady && renderControlsInLeftSidebar) {
      onControlsReady({
        isDark,
        onRefresh: refetch,
        controlsComponent,
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    onControlsReady,
    renderControlsInLeftSidebar,
  ]);
  
  return (
    <div 
      className={`flex flex-col h-full max-h-full ${isDark ? 'bg-gray-800 text-gray-100' : 'bg-gray-50 text-gray-900'}`}
    >
      <div className={`flex-shrink-0 px-6 py-4 border-b ${isDark ? 'bg-gray-800 border-gray-700' : 'bg-white border-gray-200'}`}>
        <SearchHeader 
          theme={isDark ? 'dark' : 'light'} 
          streams={streams}
          filterParams={filterParams} 
          setFilterParams={setFilterParams} 
          onUpdateSearchParams={onUpdateSearchParams} 
          addFilter={addFilter} 
          removeFilterTag={removeFilterTag} 
          filterTags={filterTags}
          isSearching={loading}
          onCancelSearch={cancelSearch}
          onGetPendingQuery={handleGetPendingQuery}
          submitChatMessage={submitChatMessage}
          contentDisabled={!chatSidebarCollapsed || loading || chatSidebarBusy}
        />
      </div>
      <div className="flex-1 overflow-auto">
        <VideoSearchList
          data={agentSearchResults ?? searchResults}
          loading={agentSearchResults !== null ? false : loading}
          error={agentSearchResults !== null ? null : error}
          isDark={isDark}
          onRefresh={refetch}
          onPlayVideo={openVideoModal}
          showObjectsBbox={mediaWithObjectsBbox}
        />
      </div>
      {/* Video Modal */}
      <VideoModal
        isOpen={videoModal.isOpen}
        videoUrl={videoModal.videoUrl}
        title={videoModal.title}
        onClose={closeVideoModal}
      />
    </div>
  );
};

// Re-export types for convenience
export type { SearchData, SearchComponentProps } from './types';

