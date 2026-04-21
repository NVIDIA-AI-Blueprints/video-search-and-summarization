import { useCallback, useContext, useEffect, useMemo } from 'react';

import { useTranslation } from 'next-i18next';

import { useCreateReducer } from '@/hooks/useCreateReducer';

import { getStorageKey } from '@/contexts/RuntimeConfigContext';
import { saveConversation, saveConversations } from '@/utils/app/conversation';
import { saveFolders } from '@/utils/app/folders';
import { exportData, importData } from '@/utils/app/importExport';

import { Conversation } from '@/types/chat';
import { LatestExportFormat, SupportedExportFormats } from '@/types/export';

import HomeContext from '@/pages/api/home/home.context';

import { ChatFolders } from './components/ChatFolders';
import { ChatbarSettings } from './components/ChatbarSettings';
import { Conversations } from './components/Conversations';

import Sidebar from '../Sidebar';
import ChatbarContext from './Chatbar.context';
import { ChatbarInitialState, initialState } from './Chatbar.state';

import { v4 as uuidv4 } from 'uuid';

interface ChatbarProps {
  renderControlsInLeftSidebar?: boolean;
  onControlsReady?: (handlers: any) => void;
}

export const Chatbar: React.FC<ChatbarProps> = ({ 
  renderControlsInLeftSidebar = false,
  onControlsReady 
}) => {
  const { t } = useTranslation('sidebar');

  const chatBarContextValue = useCreateReducer<ChatbarInitialState>({
    initialState,
  });

  const homeContext = useContext(HomeContext);

  // Extract values (with defaults if context is undefined)
  const {
    state,
    dispatch: homeDispatch,
    storageKeyPrefix,
    handleCreateFolder,
    handleNewConversation,
    handleUpdateConversation,
  } = homeContext || {};
  
  const {
    conversations = [],
    showChatbar = true,
    folders = [],
    lightMode = 'dark'
  } = state || {};

  const {
    state: { searchTerm, filteredConversations },
    dispatch: chatDispatch,
  } = chatBarContextValue;

  const handleExportData = useCallback(() => {
    exportData(storageKeyPrefix);
  }, [storageKeyPrefix]);

  const handleImportConversations = useCallback((data: SupportedExportFormats) => {
    const { history, folders, prompts }: LatestExportFormat = importData(data, storageKeyPrefix);
    homeDispatch({ field: 'conversations', value: history });
    homeDispatch({
      field: 'selectedConversation',
      value: history[history.length - 1],
    });
    homeDispatch({ field: 'folders', value: folders });
    homeDispatch({ field: 'prompts', value: prompts });

    window.location.reload();
  }, [homeDispatch, storageKeyPrefix]);

  const handleClearConversations = useCallback(() => {
    const newConversation = {
      id: uuidv4(),
      name: t('New Conversation'),
      messages: [],
      folderId: null,
    };

    homeDispatch({
      field: 'selectedConversation',
      value: newConversation,
    });

    homeDispatch({ field: 'conversations', value: [] });

    // Persist empty list and new selected conversation so refs and any re-hydration see the cleared state
    saveConversations([], storageKeyPrefix);
    saveConversation(newConversation, storageKeyPrefix);

    const updatedFolders = folders.filter((f) => f.type !== 'chat');

    homeDispatch({ field: 'folders', value: updatedFolders });
    saveFolders(updatedFolders, storageKeyPrefix);
  }, [homeDispatch, folders, t, storageKeyPrefix]);

  const handleDeleteConversation = useCallback((conversation: Conversation) => {
    const updatedConversations = conversations.filter(
      (c) => c.id !== conversation.id,
    );

    homeDispatch({ field: 'conversations', value: updatedConversations });
    chatDispatch({ field: 'searchTerm', value: '' });
    saveConversations(updatedConversations, storageKeyPrefix);

    if (updatedConversations.length > 0) {
      homeDispatch({
        field: 'selectedConversation',
        value: updatedConversations[updatedConversations.length - 1],
      });

      saveConversation(updatedConversations[updatedConversations.length - 1], storageKeyPrefix);
    } else {
      homeDispatch({
        field: 'selectedConversation',
        value: {
          id: uuidv4(),
          name: t('New Conversation'),
          messages: [],
          folderId: null,
        },
      });

      sessionStorage.removeItem(getStorageKey('selectedConversation', storageKeyPrefix));
    }
  }, [conversations, homeDispatch, chatDispatch, t, storageKeyPrefix]);

  const handleToggleChatbar = () => {
    homeDispatch({ field: 'showChatbar', value: !showChatbar });
    // Restore sessionStorage persistence - allow user to override environment variable during session
    sessionStorage.setItem(getStorageKey('showChatbar', storageKeyPrefix), JSON.stringify(!showChatbar));
  };

  const handleDrop = (e: any) => {
    if (e.dataTransfer) {
      const conversation = JSON.parse(e.dataTransfer.getData('conversation'));
      handleUpdateConversation(conversation, { key: 'folderId', value: 0 });
      chatDispatch({ field: 'searchTerm', value: '' });
      e.target.style.background = 'none';
    }
  };

  useEffect(() => {
    // Filter out homepage conversations that haven't had their first message sent
    const visibleConversations = conversations.filter(
      (conversation) => !conversation.isHomepageConversation
    );

    if (searchTerm) {
      chatDispatch({
        field: 'filteredConversations',
        value: visibleConversations.filter((conversation) => {
          const searchable =
            conversation.name.toLocaleLowerCase() +
            ' ' +
            conversation.messages.map((message) => message.content).join(' ');
          return searchable.toLowerCase().includes(searchTerm.toLowerCase());
        }),
      });
    } else {
      chatDispatch({
        field: 'filteredConversations',
        value: visibleConversations,
      });
    }
  }, [searchTerm, conversations, chatDispatch]);

  // Create stable context values for external rendering (MUST be before any early returns)
  const chatbarContextForExternal = useMemo(() => ({
    ...chatBarContextValue,
    handleDeleteConversation,
    handleClearConversations,
    handleImportConversations,
    handleExportData,
  }), [
    chatBarContextValue,
    handleDeleteConversation,
    handleClearConversations,
    handleImportConversations,
    handleExportData,
  ]);

  const homeContextForExternal = useMemo(() => {
    if (!homeContext) return null;
    return {
      state: homeContext.state,
      dispatch: homeContext.dispatch,
      handleNewConversation,
      handleCreateFolder,
      handleDeleteFolder: homeContext.handleDeleteFolder,
      handleUpdateFolder: homeContext.handleUpdateFolder,
      handleSelectConversation: homeContext.handleSelectConversation,
      handleUpdateConversation,
    };
  }, [
    homeContext,
    // Include state values to ensure recalculation when they change
    // (homeContext reference might stay same when values change)
    state,
    handleNewConversation,
    handleCreateFolder,
    handleUpdateConversation,
  ]);

  // Memoize search term change handler to prevent recreation on every render
  const handleSearchTermChange = useCallback(
    (term: string) => chatDispatch({ field: 'searchTerm', value: term }),
    [chatDispatch]
  );

  // Memoize create folder handler
  const handleCreateFolderForChat = useCallback(
    () => handleCreateFolder(t('New folder'), 'chat'),
    [handleCreateFolder, t]
  );

  // Provide control handlers to parent if specified (MUST be before any early returns)
  // This effect runs whenever onControlsReady or data changes
  useEffect(() => {
    // Only call onControlsReady if all required conditions are met
    if (onControlsReady && renderControlsInLeftSidebar && lightMode && homeContextForExternal) {
      onControlsReady({
        conversations,
        filteredConversations,
        lightMode,
        searchTerm,
        onSearchTermChange: handleSearchTermChange,
        onNewConversation: handleNewConversation,
        onCreateFolder: handleCreateFolderForChat,
        onClearConversations: handleClearConversations,
        onImportConversations: handleImportConversations,
        onExportData: handleExportData,
        // Pass contexts for internal rendering (enables reactivity)
        homeContext: homeContextForExternal,
        chatbarContext: chatbarContextForExternal,
      });
    }
  }, [
    onControlsReady,
    renderControlsInLeftSidebar,
    lightMode,
    conversations,
    filteredConversations,
    folders, // Include folders to trigger updates on folder changes
    searchTerm,
    chatbarContextForExternal,
    homeContextForExternal,
    handleNewConversation,
    handleCreateFolderForChat,
    handleClearConversations,
    handleImportConversations,
    handleExportData,
    handleSearchTermChange,
  ]);

  // Guard against undefined context - return null if not available (AFTER all hooks)
  if (!homeContext) {
    return null;
  }

  // If controls are being rendered in left sidebar externally, don't render the chatbar sidebar at all
  if (renderControlsInLeftSidebar) {
    return null;
  }

  return (
    <ChatbarContext.Provider
      value={{
        ...chatBarContextValue,
        handleDeleteConversation,
        handleClearConversations,
        handleImportConversations,
        handleExportData,
      }}
    >
      <Sidebar<Conversation>
        side={'left'}
        isOpen={showChatbar}
        addItemButtonTitle={t('New chat')}
        itemComponent={<Conversations conversations={filteredConversations} />}
        folderComponent={<ChatFolders searchTerm={searchTerm} />}
        items={filteredConversations}
        searchTerm={searchTerm}
        handleSearchTerm={(searchTerm: string) =>
          chatDispatch({ field: 'searchTerm', value: searchTerm })
        }
        toggleOpen={handleToggleChatbar}
        handleCreateItem={handleNewConversation}
        handleCreateFolder={() => handleCreateFolder(t('New folder'), 'chat')}
        handleDrop={handleDrop}
        footerComponent={<ChatbarSettings />}
        showFolderSection={folders.filter((f) => f.type === 'chat').length > 0}
      />
    </ChatbarContext.Provider>
  );
};
