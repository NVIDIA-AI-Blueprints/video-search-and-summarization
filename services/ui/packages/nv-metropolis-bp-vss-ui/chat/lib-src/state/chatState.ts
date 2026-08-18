// SPDX-License-Identifier: MIT
/**
 * Chat state shape.
 *
 * One object per mounted chat instance. Most fields are deployment
 * configuration read from env once at mount; the rest is live conversation
 * state that the streaming path updates.
 */
import type { Conversation, Message } from '../types/chat';
import type { FolderInterface } from '../types/export';

export interface ChatState {
  // Live state
  loading: boolean;
  messageIsStreaming: boolean;
  lightMode: 'light' | 'dark';
  folders: FolderInterface[];
  conversations: Conversation[];
  selectedConversation: Conversation | undefined;
  currentMessage: Message | undefined;
  currentFolder: FolderInterface | undefined;
  folderIdToExpand: string | null;
  showChatbar: boolean;
  messageError: boolean;
  searchTerm: string;
  autoScroll?: boolean;

  // Backend
  chatCompletionURL?: string;
  agentApiUrlBase?: string;
  webSocketMode?: boolean;
  webSocketConnected?: boolean;
  webSocketURL?: string;
  webSocketSchema?: string;
  webSocketSchemas?: string[];

  // Feature configuration
  chatHistory: boolean;
  enableIntermediateSteps?: boolean;
  expandIntermediateSteps?: boolean;
  intermediateStepOverride?: boolean;
  customAgentParamsJson?: string;
  chatUploadFileEnabled?: boolean;
  chatUploadFileConfigTemplateJson?: string;
  chatUploadFileMetadataEnabled?: boolean;
  chatUploadFileHiddenMessageTemplate?: string;
  chatInputMicEnabled?: boolean;
  chatMessageEditEnabled?: boolean;
  chatMessageSpeakerEnabled?: boolean;
  chatMessageCopyEnabled?: boolean;
  interactionModalCancelEnabled?: boolean;
  themeChangeButtonEnabled?: boolean;
  additionalConfig: any;
}

export const initialChatState: ChatState = {
  loading: false,
  messageIsStreaming: false,
  lightMode: 'dark',
  folders: [],
  conversations: [],
  selectedConversation: undefined,
  currentMessage: undefined,
  currentFolder: undefined,
  folderIdToExpand: null,
  showChatbar: true,
  messageError: false,
  searchTerm: '',
  chatHistory: true,
  additionalConfig: {},
};
