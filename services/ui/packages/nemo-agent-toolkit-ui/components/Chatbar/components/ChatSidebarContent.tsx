import { FC, useMemo } from 'react';
import { useTranslation } from 'next-i18next';
import { ChatSidebarControlHandlers } from '../../../pages/api/home/home';
import HomeContext from '../../../pages/api/home/home.context';
import ChatbarContext from '../Chatbar.context';
import { SidebarInner } from '../../Sidebar/SidebarInner';
import { ChatbarSettings } from './ChatbarSettings';
import { Conversations } from './Conversations';
import { ChatFolders } from './ChatFolders';

/**
 * Complete chat sidebar content component.
 * This is a pre-composed component that includes all chat sidebar elements:
 * - Action controls (New Chat, Folder, Search) at the top
 * - Conversations list in the middle (scrollable)
 * - Settings controls (Clear, Import, Export, Settings) at the bottom
 * 
 * Use this component when you want to render the complete chat sidebar
 * in an external container (e.g., main app sidebar).
 * 
 * Requires homeContext and chatbarContext for proper reactivity.
 */
export const ChatSidebarContent: FC<ChatSidebarControlHandlers> = ({
  searchTerm,
  onSearchTermChange,
  onNewConversation,
  onCreateFolder,
  conversations,
  filteredConversations,
  onClearConversations,
  onImportConversations,
  onExportData,
  homeContext,
  chatbarContext,
}) => {
  const { t } = useTranslation('sidebar');

  // Memoize conversations component to prevent unnecessary re-renders
  const itemComponent = useMemo(() => {
    if (!homeContext || !chatbarContext) return null;
    return (
      <HomeContext.Provider value={homeContext}>
        <ChatbarContext.Provider value={chatbarContext}>
          <Conversations conversations={filteredConversations} />
        </ChatbarContext.Provider>
      </HomeContext.Provider>
    );
  }, [homeContext, chatbarContext, filteredConversations]);

  // Memoize folders component
  const folderComponent = useMemo(() => {
    if (!homeContext || !chatbarContext) return null;
    return (
      <HomeContext.Provider value={homeContext}>
        <ChatbarContext.Provider value={chatbarContext}>
          <ChatFolders searchTerm={searchTerm} />
        </ChatbarContext.Provider>
      </HomeContext.Provider>
    );
  }, [homeContext, chatbarContext, searchTerm]);

  // Memoize footer component
  const footerComponent = useMemo(() => {
    const content = (
      <ChatbarSettings
        conversations={conversations}
        onClearConversations={onClearConversations}
        onImportConversations={onImportConversations}
        onExportData={onExportData}
      />
    );
    
    if (homeContext) {
      return (
        <HomeContext.Provider value={homeContext}>
          {content}
        </HomeContext.Provider>
      );
    }
    return content;
  }, [homeContext, conversations, onClearConversations, onImportConversations, onExportData]);

  const showFolderSection =
    (homeContext?.state?.folders?.filter((f: { type: string }) => f.type === 'chat').length ?? 0) > 0;

  return (
    <SidebarInner
      addItemButtonTitle={t('New chat')}
      items={filteredConversations}
      itemComponent={itemComponent}
      folderComponent={folderComponent}
      footerComponent={footerComponent}
      searchTerm={searchTerm}
      handleSearchTerm={onSearchTermChange}
      handleCreateItem={onNewConversation}
      handleCreateFolder={onCreateFolder}
      enableDragDrop={false}
      showFolderSection={showFolderSection}
    />
  );
};
