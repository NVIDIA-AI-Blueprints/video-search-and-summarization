import { Dispatch, createContext } from 'react';

import { ActionType } from '@/hooks/useCreateReducer';

import { Conversation } from '@/types/chat';
import { KeyValuePair } from '@/types/data';
import { FolderType } from '@/types/folder';

import { HomeInitialState } from './home.state';

export interface HomeContextProps {
  state: HomeInitialState;
  dispatch: Dispatch<ActionType<HomeInitialState>>;
  /** When set (e.g. "searchTab"), conversation/folder storage uses prefixed keys for this instance. */
  storageKeyPrefix?: string | null;
  handleNewConversation: (folderId?: string | null) => void;
  handleCreateFolder: (name: string, type: FolderType) => void;
  handleDeleteFolder: (folderId: string) => void;
  handleUpdateFolder: (folderId: string, name: string) => void;
  handleSelectConversation: (conversation: Conversation) => void;
  handleUpdateConversation: (
    conversation: Conversation,
    data: KeyValuePair,
  ) => void;
  /** Optional: called when a new assistant answer has finished. */
  onAnswerComplete?: () => void;
  /** Optional: called when an answer finishes, with the full assistant message text. */
  onAnswerCompleteWithContent?: (answer: string) => void;
  /** Optional: called when chat is ready; receives a function to programmatically submit a message to the agent. */
  onSubmitMessageReady?: (submitMessage: (message: string) => void) => void;
  /** Optional: called when a message is submitted programmatically (e.g. so embedder can show attention/highlight). */
  onMessageSubmitted?: () => void;
}

const HomeContext = createContext<HomeContextProps>(undefined!);

export default HomeContext;
