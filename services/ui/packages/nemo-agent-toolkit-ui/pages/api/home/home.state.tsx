import { env } from 'next-runtime-env';

import { Conversation, Message } from '@/types/chat';
import { FolderInterface } from '@/types/folder';

import { t } from 'i18next';

export interface HomeInitialState {
  loading: boolean;
  lightMode: 'light' | 'dark';
  messageIsStreaming: boolean;
  folders: FolderInterface[];
  conversations: Conversation[];
  selectedConversation: Conversation | undefined;
  currentMessage: Message | undefined;
  showChatbar: boolean;
  currentFolder: FolderInterface | undefined;
  /** When set, the folder with this id should expand (e.g. after creating a conversation in it programmatically). Cleared after expand. */
  folderIdToExpand: string | null;
  messageError: boolean;
  searchTerm: string;
  chatHistory: boolean;
  chatCompletionURL?: string;
  webSocketMode?: boolean;
  webSocketConnected?: boolean;
  webSocketURL?: string;
  webSocketSchema?: string;
  webSocketSchemas?: string[];
  enableIntermediateSteps?: boolean;
  expandIntermediateSteps?: boolean;
  intermediateStepOverride?: boolean;
  autoScroll?: boolean;
  agentApiUrlBase?: string;
  vstApiUrl?: string;
  additionalConfig: any;
  customAgentParamsJson?: string;
  chatUploadFileEnabled?: boolean;
  chatInputMicEnabled?: boolean;
  /** When false, hide the Cancel button in the WebSocket interaction (HITL) popup. Default: true. */
  interactionModalCancelEnabled?: boolean;
  chatMessageEditEnabled?: boolean;
  chatMessageSpeakerEnabled?: boolean;
  chatMessageCopyEnabled?: boolean;
  chatUploadFileConfigTemplateJson?: string;
  chatUploadFileMetadataEnabled?: boolean;
  chatUploadFileHiddenMessageTemplate?: string;
  themeChangeButtonEnabled?: boolean;
}

const getDefaultLightMode = (): 'light' | 'dark' => {
  const envValue1 = env('NEXT_PUBLIC_DARK_THEME_DEFAULT');
  const envValue2 = process?.env?.NEXT_PUBLIC_DARK_THEME_DEFAULT;
  
  // Be very explicit about checking for the exact string 'true'
  // Convert to string first to handle any unexpected types
  const envString1 = String(envValue1 || '');
  const envString2 = String(envValue2 || '');
  
  let isLightMode = true;
  if (((envString1 === 'true') || (envString2 === 'true'))) {
    isLightMode = false;
  }
  
  return isLightMode ? 'light' : 'dark';
};

const getDefaultShowChatbar = (): boolean => {
  const envValue1 = env('NEXT_PUBLIC_SIDE_CHATBAR_COLLAPSED');
  const envValue2 = process?.env?.NEXT_PUBLIC_SIDE_CHATBAR_COLLAPSED;
  
  // Convert to string first to handle any unexpected types
  const envString1 = String(envValue1 || '');
  const envString2 = String(envValue2 || '');
  
  // If environment variable is explicitly set to 'true', chatbar should be collapsed (hidden)
  // Otherwise default to showing the chatbar (not collapsed)
  if (envString1 === 'true' || envString2 === 'true') {
    return false; // Collapsed = true means showChatbar = false
  }
  
  return true; // Default to showing chatbar (not collapsed)
};

// Returns whether the chat input mic is enabled. Default: true. Set NEXT_PUBLIC_CHAT_INPUT_MIC_ENABLED=false to hide.
const getChatInputMicEnabled = (): boolean => {
  const v = env('NEXT_PUBLIC_CHAT_INPUT_MIC_ENABLED') || process?.env?.NEXT_PUBLIC_CHAT_INPUT_MIC_ENABLED;
  return String(v || '') !== 'false';
};

// Returns whether the Cancel button is shown in the WebSocket interaction (HITL) popup. Default: true. Set NEXT_PUBLIC_INTERACTION_MODAL_CANCEL_ENABLED=false to hide.
const getInteractionModalCancelEnabled = (): boolean => {
  const v = env('NEXT_PUBLIC_INTERACTION_MODAL_CANCEL_ENABLED') || process?.env?.NEXT_PUBLIC_INTERACTION_MODAL_CANCEL_ENABLED;
  return String(v || '') !== 'false';
};

const getChatMessageEditEnabled = (): boolean => {
  const v = env('NEXT_PUBLIC_CHAT_MESSAGE_EDIT_ENABLED') || process?.env?.NEXT_PUBLIC_CHAT_MESSAGE_EDIT_ENABLED;
  return String(v || '') !== 'false';
};
const getChatMessageSpeakerEnabled = (): boolean => {
  const v = env('NEXT_PUBLIC_CHAT_MESSAGE_SPEAKER_ENABLED') || process?.env?.NEXT_PUBLIC_CHAT_MESSAGE_SPEAKER_ENABLED;
  return String(v || '') !== 'false';
};
const getChatMessageCopyEnabled = (): boolean => {
  const v = env('NEXT_PUBLIC_CHAT_MESSAGE_COPY_ENABLED') || process?.env?.NEXT_PUBLIC_CHAT_MESSAGE_COPY_ENABLED;
  return String(v || '') !== 'false';
};

export const initialState: HomeInitialState = {
  loading: false,
  lightMode: getDefaultLightMode(),
  messageIsStreaming: false,
  folders: [],
  conversations: [],
  selectedConversation: undefined,
  currentMessage: undefined,
  showChatbar: getDefaultShowChatbar(),
  currentFolder: undefined,
  folderIdToExpand: null,
  messageError: false,
  searchTerm: '',
  chatHistory:
    env('NEXT_PUBLIC_CHAT_HISTORY_DEFAULT_ON') === 'true' ||
    process?.env?.NEXT_PUBLIC_CHAT_HISTORY_DEFAULT_ON === 'true'
      ? true
      : false,
  chatCompletionURL:
    env('NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL') ||
    process?.env?.NEXT_PUBLIC_HTTP_CHAT_COMPLETION_URL ||
    'http://127.0.0.1:8000/chat/stream',
  webSocketMode:
    env('NEXT_PUBLIC_WEB_SOCKET_DEFAULT_ON') === 'true' ||
    process?.env?.NEXT_PUBLIC_WEB_SOCKET_DEFAULT_ON === 'true'
      ? true
      : false,
  webSocketConnected: false,
  webSocketURL:
    env('NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL') ||
    process?.env?.NEXT_PUBLIC_WEBSOCKET_CHAT_COMPLETION_URL ||
    'ws://127.0.0.1:8000/websocket',
  webSocketSchema: 'chat_stream',
  webSocketSchemas: ['chat_stream', 'chat', 'generate_stream', 'generate'],
  enableIntermediateSteps:
    env('NEXT_PUBLIC_ENABLE_INTERMEDIATE_STEPS') === 'true' ||
    process?.env?.NEXT_PUBLIC_ENABLE_INTERMEDIATE_STEPS === 'true'
      ? true
      : false,
  expandIntermediateSteps: false,
  intermediateStepOverride: true,
  autoScroll: true,
  agentApiUrlBase:
    env('NEXT_PUBLIC_AGENT_API_URL_BASE') ||
    process?.env?.NEXT_PUBLIC_AGENT_API_URL_BASE ||
    '',
  // Base URL for VST's REST API (e.g. http://host:30888/vst/api). Shared
  // with the alerts/search/video-management tabs (same NEXT_PUBLIC_VST_API_URL
  // env var). Used by ChatFileUpload to POST chunks directly to
  // /v1/storage/file before notifying the agent's /complete hook for
  // post-processing.
  vstApiUrl:
    env('NEXT_PUBLIC_VST_API_URL') ||
    process?.env?.NEXT_PUBLIC_VST_API_URL ||
    '',
  additionalConfig: {},
  customAgentParamsJson:
    env('NEXT_PUBLIC_CHAT_API_CUSTOM_AGENT_PARAMS_JSON') ||
    process?.env?.NEXT_PUBLIC_CHAT_API_CUSTOM_AGENT_PARAMS_JSON ||
    '',
  chatUploadFileEnabled:
    env('NEXT_PUBLIC_CHAT_UPLOAD_FILE_ENABLE') === 'true' ||
    process?.env?.NEXT_PUBLIC_CHAT_UPLOAD_FILE_ENABLE === 'true'
      ? true
      : false,
  chatInputMicEnabled: getChatInputMicEnabled(),
  interactionModalCancelEnabled: getInteractionModalCancelEnabled(),
  chatMessageEditEnabled: getChatMessageEditEnabled(),
  chatMessageSpeakerEnabled: getChatMessageSpeakerEnabled(),
  chatMessageCopyEnabled: getChatMessageCopyEnabled(),
  chatUploadFileConfigTemplateJson:
    env('NEXT_PUBLIC_CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON') ||
    process?.env?.NEXT_PUBLIC_CHAT_UPLOAD_FILE_CONFIG_TEMPLATE_JSON ||
    '',
  chatUploadFileMetadataEnabled:
    env('NEXT_PUBLIC_CHAT_UPLOAD_FILE_METADATA_ENABLED') === 'true' ||
    process?.env?.NEXT_PUBLIC_CHAT_UPLOAD_FILE_METADATA_ENABLED === 'true'
      ? true
      : false,
  chatUploadFileHiddenMessageTemplate:
    env('NEXT_PUBLIC_CHAT_UPLOAD_FILE_HIDDEN_MESSAGE_TEMPLATE') ||
    process?.env?.NEXT_PUBLIC_CHAT_UPLOAD_FILE_HIDDEN_MESSAGE_TEMPLATE ||
    '',
  themeChangeButtonEnabled:
    (env('NEXT_PUBLIC_SHOW_THEME_TOGGLE_BUTTON') ||
      process?.env?.NEXT_PUBLIC_SHOW_THEME_TOGGLE_BUTTON ||
      'true') !== 'false',
};
