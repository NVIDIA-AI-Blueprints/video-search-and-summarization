// Manual type declarations for exported components
import React from 'react';

// VideoModal component and props
export interface VideoModalProps {
  isOpen: boolean;
  videoUrl: string;
  title: string;
  onClose: () => void;
}

export const VideoModal: React.FC<VideoModalProps>;

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

export interface NemoAgentToolkitAppProps {
  theme?: 'light' | 'dark';
  onThemeChange?: (theme: 'light' | 'dark') => void;
  isActive?: boolean;
  initialStateOverride?: Partial<HomeInitialState>;
  /** Optional storage key prefix (e.g. "searchTab") so this instance uses separate sessionStorage; pass at instantiation for reusability. */
  storageKeyPrefix?: string;
  renderControlsInLeftSidebar?: boolean;
  renderApplicationHead?: boolean;
  onControlsReady?: (handlers: ChatSidebarControlHandlers) => void;
  /** Optional: called when a new assistant answer has finished. */
  onAnswerComplete?: () => void;
  /** Optional: called when an answer finishes, with the full assistant message text. */
  onAnswerCompleteWithContent?: (answer: string) => void;
  /** Optional: called when chat is ready; receives a function to programmatically submit a message to the agent. */
  onSubmitMessageReady?: (submitMessage: (message: string) => void) => void;
  /** Optional: called when a message is submitted programmatically (e.g. for attention/highlight). */
  onMessageSubmitted?: () => void;
  className?: string;
  style?: React.CSSProperties;
}

export const NemoAgentToolkitApp: React.ComponentType<NemoAgentToolkitAppProps>;

// Individual components
export const Chat: React.ComponentType<any>;
export const Chatbar: React.ComponentType<any>;
export const ChatInput: React.ComponentType<any>;
export const ChatMessage: React.ComponentType<any>;

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

// Utils
export function formatTimestamp(timestamp: string | number): string;
export function copyToClipboard(text: string): Promise<void>;

// Video Upload Utils
export interface FileUploadResult {
  filename: string;
  bytes: number;
  sensorId: string;
  streamId: string;
  filePath: string;
  timestamp: string;
}

export function getUploadUrl(
  filename: string,
  uploadUrl: string,
  formData?: Record<string, any>,
  signal?: AbortSignal
): Promise<string>;

export function uploadFile(
  file: File,
  uploadUrl: string,
  formData: Record<string, any>,
  onProgress?: (progress: number) => void,
  abortSignal?: AbortSignal
): Promise<FileUploadResult>;

// Re-export next-i18next config
export const nextI18nConfig: any;

