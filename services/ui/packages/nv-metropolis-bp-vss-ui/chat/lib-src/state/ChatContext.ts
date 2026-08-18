// SPDX-License-Identifier: MIT
/**
 * Context shared by every part of one chat instance.
 *
 * Carries state and dispatch plus the callbacks an embedding app supplies —
 * how the host learns an answer finished, and how it hands the chat a way to
 * submit messages on its behalf.
 */
import { createContext, type Dispatch } from 'react';

import type { ChatVideoUploadCompletePayload } from 'common';
import type { CallerInfo, Conversation, QueryDataContext } from '../types/chat';
import type { FolderType } from '../types/export';
import type { ChatState } from './chatState';

/** Reducer action: assign `value` to `field`. */
export interface ChatAction<T> {
  type: 'change' | 'reset';
  field?: keyof T;
  value?: any;
}

export interface ChatContextProps {
  state: ChatState;
  dispatch: Dispatch<ChatAction<ChatState>>;
  /** Namespaces this instance's stored conversations and folders. */
  storageKeyPrefix?: string | null;

  handleNewConversation: (folderId?: string | null) => void;
  handleCreateFolder: (name: string, type: FolderType) => void;
  handleDeleteFolder: (folderId: string) => void;
  handleUpdateFolder: (folderId: string, name: string) => void;
  handleSelectConversation: (conversation: Conversation) => void;
  handleUpdateConversation: (
    conversation: Conversation,
    data: { key: string; value: any },
  ) => void;

  /** Called when an assistant answer finishes. */
  onAnswerComplete?: () => void;
  /** Called with the finished answer; may return HTML to render as caller info. */
  onAnswerCompleteWithContent?: (answer: string) => CallerInfo | void;
  /** Hands the host a way to submit a message programmatically. */
  onSubmitMessageReady?: (submitMessage: (message: string) => void) => void;
  /** Called when a message is submitted, so the host can draw attention to it. */
  onMessageSubmitted?: () => void;
  /** Hands the host a way to attach a context chip to the composer. */
  onAddQueryContextReady?: (addItem: (item: QueryDataContext) => void) => void;
  /** Called once per upload batch that produced at least one success. */
  onChatVideoUploadComplete?: (payload: ChatVideoUploadCompletePayload) => void;
}

const ChatContext = createContext<ChatContextProps>(undefined!);

export default ChatContext;
