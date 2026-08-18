// SPDX-License-Identifier: MIT
/**
 * Conversation export formats.
 *
 * Older exports stay importable, so every historical shape is retained and
 * upgraded to the latest on import rather than rejected.
 */
import type { Conversation, Message } from './chat';

export interface FolderInterface {
  id: string;
  name: string;
  type: FolderType;
}

export type FolderType = 'chat' | 'prompt';

export interface Prompt {
  id: string;
  name: string;
  description: string;
  content: string;
  folderId?: string | null;
}

/** v1: a bare array of conversations, numeric ids, no folders. */
interface ConversationV1 {
  id: number;
  name: string;
  messages: Message[];
}

export type ExportFormatV1 = ConversationV1[];

/** v2: history and folders, still unversioned and numerically keyed. */
interface ChatFolder {
  id: number;
  name: string;
}

export interface ExportFormatV2 {
  history: Conversation[] | null;
  folders: ChatFolder[] | null;
}

/** v3: first versioned format, string ids. */
export interface ExportFormatV3 {
  version: 3;
  history: Conversation[];
  folders: FolderInterface[];
}

/** v4: adds saved prompts. */
export interface ExportFormatV4 {
  version: 4;
  history: Conversation[];
  folders: FolderInterface[];
  prompts: Prompt[];
}

export type SupportedExportFormats =
  | ExportFormatV1
  | ExportFormatV2
  | ExportFormatV3
  | ExportFormatV4;

export type LatestExportFormat = ExportFormatV4;
