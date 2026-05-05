// Manual type declarations for exported components
import React from 'react';

// VideoModal & useVideoModal (re-exported from common)
export {
  VideoModal,
  type VideoModalProps,
  useVideoModal,
  type VideoModalState,
  type VideoModalData,
  type AlertLike,
  type UseVideoModalOptions,
} from '@aiqtoolkit-ui/common';

// Main app export and types
export interface ChatSidebarControlHandlers {
  conversations: any[];
  filteredConversations: any[];
  lightMode: 'light' | 'dark';
  searchTerm: string;
  onSearchTermChange: (term: string) => void;
  onNewConversation: () => void;
  onCreateFolder: () => void;
  onClearConversations: () => void;
  onImportConversations: (data: any) => void;
  onExportData: () => void;
  // Context values for internal rendering (enables reactivity)
  homeContext?: any;
  chatbarContext?: any;
}

/**
 * Parent-provided HTML string shown under an assistant response card via `dangerouslySetInnerHTML`.
 *
 * **Security:** `CallerInfo` is treated as HTML. Values returned from `onAnswerCompleteWithContent`
 * MUST be sanitized by the parent before they are returned, or XSS can occur when the UI renders
 * them. Prefer a robust HTML sanitizer (for example, DOMPurify) on any string that is not already
 * known-safe static markup. Only pass trusted content or content you have explicitly sanitized.
 */
export type CallerInfo = string;

export interface NemoAgentToolkitAppProps {
  theme: string;
  onThemeChange?: (theme: string) => void;
  isActive?: boolean;
  initialStateOverride?: Partial<HomeInitialState>;
  /** Optional storage key prefix (e.g. "searchTab") so this instance uses separate sessionStorage; pass at instantiation for reusability. */
  storageKeyPrefix?: string;
  renderControlsInLeftSidebar?: boolean;
  renderApplicationHead?: boolean;
  onControlsReady?: (handlers: ChatSidebarControlHandlers) => void;
  /** Optional: called when a new assistant answer has finished. */
  onAnswerComplete?: () => void;
  /** Optional: called when an answer finishes; may return a renderable HTML string for parent-app caller info. */
  onAnswerCompleteWithContent?: (answer: string) => CallerInfo | void;
  /** Optional: called when chat is ready; receives a function to programmatically submit a message to the agent. */
  onSubmitMessageReady?: (submitMessage: (message: string) => void) => void;
  /** Optional: called when a message is submitted programmatically (e.g. for attention/highlight). */
  onMessageSubmitted?: () => void;
  /** Optional: called when chat is ready; receives a function the embedder can call to add a query context item to the chat input. */
  onAddQueryContextReady?: (addItem: (item: { id: string; label: string; type: string; data: Record<string, unknown> }) => void) => void;
}

export const NemoAgentToolkitApp: React.ComponentType<NemoAgentToolkitAppProps>;

// Individual components
export const Chat: React.ComponentType<any>;
export const Chatbar: React.ComponentType<any>;
export const ChatInput: React.ComponentType<any>;
export const ChatMessage: React.ComponentType<any>;

export interface UploadFileFieldConfig {
  'field-name': string;
  'field-type': 'boolean' | 'string' | 'number' | 'array' | 'select';
  'field-default-value': boolean | string | number | string[] | number[];
  'field-options'?: string[] | number[];
  changeable?: boolean;
  'tooltip-info'?: string;
}
export interface UploadFileConfigTemplate {
  fields: UploadFileFieldConfig[];
}
export interface UploadFilesDialogFileItem {
  id: string;
  file: File;
  formData: Record<string, any>;
  isExpanded: boolean;
  uploadFilename?: string;
  metadataFile?: File | null;
  isMetadataExpanded?: boolean;
}
export interface UploadFilesDialogEntry {
  id: string;
  file: File;
  formData: Record<string, any>;
  uploadFilename?: string;
  metadataFile?: File | null;
}
export interface UploadFilesDialogMetadataConfig {
  enabled: true;
  validateMetadataFile?: (file: File) => Promise<boolean>;
}
export interface UploadFilesDialogOptions {
  title?: string;
  emptyStateHint?: React.ReactNode;
  addMoreWithIcon?: boolean;
}
export interface UploadFilesDialogHandle {
  open: (files?: File[]) => void;
  close: () => void;
}
export interface UploadFilesDialogProps {
  open?: boolean;
  configTemplate: UploadFileConfigTemplate | null;
  onClose: () => void;
  onConfirm: (entries: UploadFilesDialogEntry[]) => void;
  initialFiles?: File[] | null;
  accept?: string;
  validateFile?: (file: File) => boolean;
  metadata?: UploadFilesDialogMetadataConfig;
  options?: UploadFilesDialogOptions;
}
export const UploadFilesDialog: React.ForwardRefExoticComponent<
  UploadFilesDialogProps & React.RefAttributes<UploadFilesDialogHandle>
>;

// Chat sidebar (for external rendering)
export const ChatSidebarContent: React.ComponentType<ChatSidebarControlHandlers>;

// Context
export const HomeContext: React.Context<any>;
export interface HomeContextProps {
  [key: string]: any;
}

export interface RuntimeConfig {
  workflow?: string;
  rightMenuOpen?: boolean;
  /** When set, conversation/folder storage uses prefixed keys so multiple instances keep separate history. */
  storageKeyPrefix?: string;
}
export interface RuntimeConfigProviderProps {
  value?: RuntimeConfig;
  children: React.ReactNode;
}
export const RuntimeConfigProvider: React.FC<RuntimeConfigProviderProps>;
export function useRuntimeConfig(): RuntimeConfig | undefined;
export function useWorkflowName(): string;
export function useRightMenuOpenDefault(): boolean;

export interface HomeInitialState {
  [key: string]: any;
}

export const initialState: HomeInitialState;

// Types
export interface Conversation {
  [key: string]: any;
}

export interface Message {
  [key: string]: any;
}

export interface ChatBody {
  [key: string]: any;
}

export interface FolderInterface {
  [key: string]: any;
}

export type FolderType = string;

export interface KeyValuePair {
  [key: string]: any;
}

// Hooks
export function useCreateReducer(): any;

// Utils (re-exported from common)
export {
  copyToClipboard,
  formatTimestamp,
  getUploadUrl,
  uploadFile,
  uploadFileChunked,
  uploadFileChunkedViaAgent,
  notifyGenericUploadComplete,
  CHUNK_SIZE_BYTES,
  MAX_CHUNK_RETRIES,
  type FileUploadResult,
  type ChunkedUploadOptions,
  type ChunkedUploadResponse,
} from '@aiqtoolkit-ui/common';

// Re-export next-i18next config
export const nextI18nConfig: any;

