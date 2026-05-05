// Export the entire app as importable components
import { GetServerSideProps } from 'next';

export { default as nextI18nConfig } from './next-i18next.config';

// Main app export
export { default as NemoAgentToolkitApp } from './pages/api/home/home';
export type { NemoAgentToolkitAppProps, ChatSidebarControlHandlers } from './pages/api/home/home';

// Individual components
export { Chat } from './components/Chat/Chat';
export { Chatbar } from './components/Chatbar/Chatbar';
export { ChatInput } from './components/Chat/ChatInput';
export { ChatMessage } from './components/Chat/ChatMessage';
export {
  VideoModal,
  type VideoModalProps,
  useVideoModal,
  type VideoModalState,
  type VideoModalData,
  type AlertLike,
  type UseVideoModalOptions,
  UploadFilesDialog,
  type UploadFilesDialogFileItem,
  type UploadFilesDialogEntry,
  type UploadFilesDialogProps,
  type UploadFilesDialogHandle,
  type UploadFilesDialogMetadataConfig,
  type UploadFilesDialogOptions,
  type UploadFileConfigTemplate,
  type UploadFileFieldConfig,
} from '@aiqtoolkit-ui/common';

// Chat sidebar (for external rendering)
export { ChatSidebarContent } from './components/Chatbar/components/ChatSidebarContent';

// Context
export { default as HomeContext } from './pages/api/home/home.context';
export type { HomeContextProps } from './pages/api/home/home.context';
export {
  RuntimeConfigProvider,
  useRuntimeConfig,
  useWorkflowName,
  useRightMenuOpenDefault,
  getStorageKey,
} from './contexts/RuntimeConfigContext';
export type { RuntimeConfig, RuntimeConfigProviderProps } from './contexts/RuntimeConfigContext';
export { initialState, type HomeInitialState } from './pages/api/home/home.state';

// Types
export type { Conversation, Message, ChatBody, CallerInfo } from './types/chat';
export type { FolderInterface, FolderType } from './types/folder';
export type { KeyValuePair } from './types/data';

// Hooks
export { useCreateReducer } from './hooks/useCreateReducer';

// Utils
export * from './utils/app/conversation';
export * from './utils/app/settings';
export * from './utils/app/clean';
export * from './utils/app/folders';
export * from './utils/app/helper';
export {
  copyToClipboard,
  formatTimestamp,
  getUploadUrl,
  uploadFile,
  uploadFileChunked,
  uploadFileChunkedToVst,
  notifyGenericUploadComplete,
  CHUNK_SIZE_BYTES,
  MAX_CHUNK_RETRIES,
  type FileUploadResult,
  type ChunkedUploadOptions,
  type ChunkedUploadResponse,
} from '@aiqtoolkit-ui/common';

// Constants
export * from './constants/constants';
