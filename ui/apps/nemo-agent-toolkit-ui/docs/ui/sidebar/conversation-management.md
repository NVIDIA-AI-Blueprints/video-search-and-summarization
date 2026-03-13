# Sidebar Conversation Management

## Purpose
The sidebar provides conversation navigation, organization through folders, and conversation management features including search, import/export, and settings access for the NeMo Agent toolkit UI.

## Scope
- Route(s): Available on all pages (persistent sidebar)
- Primary components: `Chatbar`, `Sidebar`, `Conversations`, `ChatFolders`, `ChatbarSettings`
- External deps: Local storage for conversation persistence, drag & drop API

## UI Elements

| Element | Type | Location | Action/Handler | Notes |
|--------|------|----------|----------------|-------|
| Toggle Sidebar | Button | Top Right | handleToggleChatbar | Shows/hides left sidebar |
| New Chat | Button | Sidebar Header | handleNewConversation | Creates new conversation |
| New Folder | Button | Sidebar Header | handleCreateFolder | Creates chat folder |
| Search Input | Input | Sidebar Top | handleSearchTerm | Filters conversations by name/content |
| Conversation Item | Button | Main Area | handleSelectConversation | Switches to conversation |
| Folder | Collapsible | Main Area | toggleFolder | Organize conversations |
| Settings | Button | Sidebar Footer | Opens settings modal | Configure API endpoints |
| Import/Export | Buttons | Settings | Import/export data | JSON format conversation backup |
| Clear All | Button | Settings | handleClearConversations | Removes all conversations |

## Component Tree
```
<Chatbar>
├─ <ChatbarContext.Provider>
│  └─ <Sidebar>
│     ├─ <Search /> (searchTerm handling)
│     ├─ <div className="items-container">
│     │  ├─ <ChatFolders>
│     │  │  └─ <Folder> (for each folder)
│     │  │     └─ <ConversationComponent> (conversations in folder)
│     │  └─ <Conversations>
│     │     └─ <ConversationComponent> (unfiled conversations)
│     └─ <ChatbarSettings>
│        ├─ Import Button
│        ├─ Export Button
│        └─ Clear Conversations Button
```

## Behavior

**Conversation Management:**
- New conversations appear at top of list
- Clicking conversation switches active chat
- Conversations persist in local storage
- Search filters by conversation name and message content

**Folder Organization:**
- Drag conversations onto folders to organize
- Folders can be created, renamed, and deleted
- Conversations in folders are indented visually
- Deleting folder moves conversations back to main list

**Search Functionality:**
- Real-time filtering as user types
- Searches conversation names and message content
- Clear button removes search filter
- No results message when no matches found

**Import/Export:**
- Export downloads JSON file with all conversation data
- Import accepts JSON files and replaces current data
- Clear all removes all conversations with confirmation
- Data persisted across browser sessions

## Source Links
- [components/Chatbar/Chatbar.tsx](../../../components/Chatbar/Chatbar.tsx)
- [components/Chatbar/components/Conversations.tsx](../../../components/Chatbar/components/Conversations.tsx)
- [components/Chatbar/components/ChatFolders.tsx](../../../components/Chatbar/components/ChatFolders.tsx)
- [components/Chatbar/components/Conversation.tsx](../../../components/Chatbar/components/Conversation.tsx)
- [components/Chatbar/components/ChatbarSettings.tsx](../../../components/Chatbar/components/ChatbarSettings.tsx)
- [components/Sidebar/Sidebar.tsx](../../../components/Sidebar/Sidebar.tsx)
- [components/Folder/Folder.tsx](../../../components/Folder/Folder.tsx)